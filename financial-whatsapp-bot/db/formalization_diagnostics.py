"""Persistencia HDU017 en el Supabase de usuarios mediante RPC transaccionales.

No utiliza DB_DSN ni el pool del RAG. Cada operación hace una sola petición;
las escrituras, bloqueos y snapshots se resuelven juntos dentro de PostgreSQL.
Desde async debe invocarse mediante to_thread/threadpool, como el resto del flujo.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from schemas.formalization_diagnostics import (
    FormalizationAssessmentInput,
    FormalizationDraftPatch,
    FormalizationItemInput,
)


class FormalizationPersistenceError(RuntimeError):
    """El fallo de almacenamiento no equivale a datos vacíos."""


class FormalizationConnectionError(FormalizationPersistenceError):
    """El cliente administrativo de Supabase no está disponible."""


class FormalizationConflictError(FormalizationPersistenceError):
    """Borrador, fuentes o solicitud cambiaron: recargar antes de continuar."""


class FormalizationNotFoundError(FormalizationPersistenceError):
    """El registro no existe para el usuario indicado."""


def _admin_client():
    import dependencies

    if dependencies.supabase_admin is None:
        raise FormalizationConnectionError(
            "El diagnóstico requiere SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY. "
            "No se utilizará la conexión del RAG como alternativa."
        )
    return dependencies.supabase_admin


def _uuid(value: str | UUID) -> str:
    return str(UUID(str(value)))


def _today() -> date:
    return datetime.now(ZoneInfo("America/Santiago")).date()


def _validate_period(start: date | None, end: date | None) -> None:
    if (start is None) != (end is None):
        raise ValueError("El periodo requiere inicio y fin")
    if start is not None and not 0 < (end - start).days <= 366:
        raise ValueError("El periodo debe ser semiabierto y abarcar entre 1 y 366 días")


def _version(value: int) -> int:
    if type(value) is not int or not 0 <= value <= 9_223_372_036_854_775_807:
        raise ValueError("Versión esperada inválida")
    return value


def _payload(model, *, exclude_unset: bool = False) -> dict:
    # Pydantic puede convertir float NaN/Infinity a null en modo JSON.
    # Rechazarlos ANTES de serializar evita confundirlos con un costo desconocido.
    def check(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Importe no finito")
        if isinstance(value, Decimal) and not value.is_finite():
            raise ValueError("Importe no finito")
        if isinstance(value, dict):
            for child in value.values():
                check(child)
        elif isinstance(value, (list, tuple, set, frozenset)):
            for child in value:
                check(child)
    check(model.model_dump(mode="python", exclude_unset=exclude_unset))
    return model.model_dump(mode="json", exclude_unset=exclude_unset)


def _rpc(name: str, parameters: dict) -> dict:
    # También detecta NaN/Infinity dentro de answers, evidence o result.
    json.dumps(parameters, allow_nan=False)
    try:
        data = _admin_client().rpc(name, parameters).execute().data
    except Exception as error:
        code = getattr(error, "code", None)
        if code in {"40001", "40P01", "23505"}:
            raise FormalizationConflictError("La evaluación cambió; recarga el contexto") from error
        if code in {"02000", "P0002"}:
            raise FormalizationNotFoundError("No se encontró el diagnóstico o perfil") from error
        raise
    # jsonb escalar; tolerar la representación de una sola fila del cliente.
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if not isinstance(data, dict):
        raise FormalizationPersistenceError("La función del diagnóstico devolvió un formato inválido")
    return data


def _context(data: dict) -> dict:
    if ("diagnostic" not in data or
            (data["diagnostic"] is not None and not isinstance(data["diagnostic"], dict)) or
            not isinstance(data.get("items"), list) or
            any(not isinstance(item, dict) for item in data["items"]) or
            ("fresh_sources" in data and not isinstance(data["fresh_sources"], dict))):
        raise FormalizationPersistenceError("El contexto del diagnóstico está incompleto")
    return data


def get_formalization_estimate_catalog(*, version: str | None = None,
                                       include_inactive: bool = False) -> dict | None:
    """Lee una versión completa del catálogo en una petición al Supabase de usuarios.

    El uso normal solo devuelve la versión activa. Una versión inactiva se puede
    inspeccionar expresamente durante las pruebas, sin habilitarla para usuarios.
    """
    query = _admin_client().table("formalization_estimate_catalog_versions").select(
        "id,version,author,prepared_at,review_by,document,active"
    )
    if version is not None:
        if not isinstance(version, str) or not version.strip() or len(version) > 120:
            raise ValueError("Versión de estimaciones inválida")
        query = query.eq("version", version)
    if not include_inactive:
        query = query.eq("active", True)
    rows = query.limit(1).execute().data or []
    if not rows:
        return None
    row = rows[0]
    if (not isinstance(row, dict) or not isinstance(row.get("document"), dict)
            or row["document"].get("version") != row.get("version")):
        raise FormalizationPersistenceError("La versión de estimaciones está incompleta")
    return row


def get_formalization_context(
    user_id: str, diagnostic_id: str | None = None, *, refresh_sources: bool = False,
    period_start: date | None = None, period_end: date | None = None, as_of: date | None = None,
) -> dict:
    """Lee cabecera y partidas; las fuentes solo se consultan si se solicitan.

    El refresco se devuelve aparte, sin sobrescribir lo confirmado ni guardar.
    """
    _validate_period(period_start, period_end)
    if period_start is not None and not refresh_sources:
        raise ValueError("El periodo alternativo requiere refresh_sources=True")
    return _context(_rpc("get_formalization_context_v1", {
        "p_user_id": _uuid(user_id),
        "p_diagnostic_id": _uuid(diagnostic_id) if diagnostic_id else None,
        "p_refresh_sources": refresh_sources,
        "p_period_start": period_start.isoformat() if period_start else None,
        "p_period_end": period_end.isoformat() if period_end else None,
        "p_as_of": (as_of or _today()).isoformat(),
    }))


def save_formalization_draft(
    user_id: str, diagnostic_id: str, expected_lock_version: int,
    patch: FormalizationDraftPatch | dict | None = None, *,
    items: list[FormalizationItemInput | dict] | None = None, delete_item_keys: list[str] | None = None,
    refresh_sources: bool = False, reopen: bool = False, as_of: date | None = None,
) -> dict:
    """Guarda el lote completo en una transacción con control de versión.

    Crear requiere UUID del backend y versión 0. No reintenta un cambio obsoleto.
    """
    model = patch if isinstance(patch, FormalizationDraftPatch) else FormalizationDraftPatch.model_validate(patch or {})
    inputs = [item if isinstance(item, FormalizationItemInput) else FormalizationItemInput.model_validate(item)
              for item in (items or [])]
    deleted = delete_item_keys or []
    if len(inputs) > 200 or len(deleted) > 200:
        raise ValueError("Máximo 200 cambios por lote")
    if any(not isinstance(key, str) or not key.strip() or len(key) > 120 for key in deleted):
        raise ValueError("Clave de partida inválida")
    keys = [item.item_key for item in inputs]
    if len(set(keys)) != len(keys) or len(set(deleted)) != len(deleted) or set(deleted).intersection(keys):
        raise ValueError("Una clave no puede repetirse ni eliminarse y guardarse en el mismo lote")
    data = _context(_rpc("save_formalization_draft_v1", {
        "p_user_id": _uuid(user_id), "p_diagnostic_id": _uuid(diagnostic_id),
        "p_expected_lock_version": _version(expected_lock_version),
        "p_patch": _payload(model, exclude_unset=True),
        "p_items": [_payload(item) for item in inputs],
        "p_delete_item_keys": deleted, "p_refresh_sources": refresh_sources,
        "p_reopen": reopen, "p_as_of": (as_of or _today()).isoformat(),
    }))
    if data["diagnostic"] is None:
        raise FormalizationPersistenceError("El guardado no devolvió el diagnóstico")
    return data


def confirm_formalization_revision(
    user_id: str, diagnostic_id: str, expected_lock_version: int, request_id: str,
    assessment: FormalizationAssessmentInput | dict, *, as_of: date | None = None,
) -> dict:
    """Confirma revisión y resultado atómicamente; reintentos idénticos son idempotentes."""
    model = assessment if isinstance(assessment, FormalizationAssessmentInput) else FormalizationAssessmentInput.model_validate(assessment)
    payload = _payload(model)
    if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 512_000:
        raise ValueError("Resultado demasiado grande")
    data = _rpc("confirm_formalization_revision_v1", {
        "p_user_id": _uuid(user_id), "p_diagnostic_id": _uuid(diagnostic_id),
        "p_expected_lock_version": _version(expected_lock_version), "p_request_id": _uuid(request_id),
        "p_assessment": payload, "p_as_of": (as_of or _today()).isoformat(),
    })
    if (not isinstance(data.get("revision"), dict) or type(data.get("replayed")) is not bool or
            (not data["replayed"] and not isinstance(data.get("diagnostic"), dict))):
        raise FormalizationPersistenceError("La confirmación no devolvió una revisión válida")
    return data
