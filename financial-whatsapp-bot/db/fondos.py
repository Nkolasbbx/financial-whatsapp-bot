"""Persistencia del flujo conversacional de fondos concursables (HdU05).

Este módulo concentra el acceso a las tablas ``fondos``,
``fund_requirement_definitions``, ``fund_sessions`` y
``fund_user_answers``. Las reglas de elegibilidad permanecen fuera de esta
capa: aquí solamente se consultan y persisten datos.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any


ACTIVE_SESSION_STATUSES = {"selecting", "collecting_data"}
VALID_SESSION_STATUSES = ACTIVE_SESSION_STATUSES | {"evaluated", "cancelled"}
UNKNOWN_ANSWER = {"status": "unknown"}


def _admin_client():
    import dependencies

    if dependencies.supabase_admin is None:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY no está configurada; "
            "no se puede usar el flujo de fondos"
        )
    return dependencies.supabase_admin


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_fund_text(value: str | None) -> str:
    """Normaliza un nombre de fondo para comparaciones tolerantes."""
    if not value:
        return ""

    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    normalized = normalized.casefold().replace("_", " ")
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def list_active_funds() -> list[dict]:
    """Retorna fondos activos ordenados por fecha de cierre."""
    result = (
        _admin_client()
        .table("fondos")
        .select("*")
        .eq("activo", True)
        .order("fecha_cierre", desc=False)
        .execute()
    )
    return result.data or []


def get_fund_by_id(fund_id: str) -> dict | None:
    """Obtiene un fondo por UUID, aunque se encuentre inactivo."""
    result = (
        _admin_client()
        .table("fondos")
        .select("*")
        .eq("id", fund_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def find_active_fund(value: str | None) -> dict | None:
    """Busca un fondo activo por UUID, slug, nombre o alias.

    Los fondos activos son pocos y ya se necesitan para construir la lista de
    WhatsApp. Resolver los aliases localmente mantiene una comparación única y
    tolerante a tildes, guiones, mayúsculas y espacios.
    """
    search = normalize_fund_text(value)
    if not search:
        return None

    for fund in list_active_funds():
        candidates = [
            fund.get("id"),
            fund.get("slug"),
            fund.get("nombre"),
            *(fund.get("aliases") or []),
        ]
        if any(normalize_fund_text(candidate) == search for candidate in candidates):
            return fund

    return None


def get_requirement_definitions(
    field_keys: list[str] | set[str] | tuple[str, ...] | None = None,
) -> dict[str, dict]:
    """Retorna definiciones indexadas por ``field_key``.

    El filtrado se hace localmente porque el catálogo es pequeño y así se
    mantiene una única consulta compatible con distintas versiones del cliente
    de Supabase.
    """
    result = (
        _admin_client()
        .table("fund_requirement_definitions")
        .select("*")
        .order("question_order", desc=False)
        .execute()
    )
    definitions = {
        row["field_key"]: row
        for row in (result.data or [])
        if row.get("field_key")
    }

    if field_keys is None:
        return definitions

    requested = set(field_keys)
    return {
        field_key: definition
        for field_key, definition in definitions.items()
        if field_key in requested
    }


def get_active_fund_session(user_id: str) -> dict | None:
    """Obtiene la sesión activa del usuario, si existe."""
    result = (
        _admin_client()
        .table("fund_sessions")
        .select("*")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None

    session = result.data[0]
    return session if session.get("status") in ACTIVE_SESSION_STATUSES else None


def start_fund_session(user_id: str, fund_id: str | None = None) -> dict | None:
    """Crea o reinicia la única sesión de fondos del usuario."""
    now = _utc_now_iso()
    payload = {
        "user_id": user_id,
        "fondo_id": fund_id,
        "status": "collecting_data" if fund_id else "selecting",
        "pending_field_key": None,
        "updated_at": now,
    }
    result = (
        _admin_client()
        .table("fund_sessions")
        .upsert(payload, on_conflict="user_id")
        .execute()
    )
    return result.data[0] if result.data else None


def update_fund_session(
    user_id: str,
    *,
    fund_id: str | None = None,
    status: str | None = None,
    pending_field_key: str | None = None,
    clear_pending_field: bool = False,
) -> dict | None:
    """Actualiza los campos entregados de una sesión existente."""
    if status is not None and status not in VALID_SESSION_STATUSES:
        raise ValueError(f"Estado de sesión de fondos inválido: {status}")

    changes: dict[str, Any] = {"updated_at": _utc_now_iso()}
    if fund_id is not None:
        changes["fondo_id"] = fund_id
    if status is not None:
        changes["status"] = status
    if pending_field_key is not None:
        changes["pending_field_key"] = pending_field_key
    elif clear_pending_field:
        changes["pending_field_key"] = None

    result = (
        _admin_client()
        .table("fund_sessions")
        .update(changes)
        .eq("user_id", user_id)
        .execute()
    )
    return result.data[0] if result.data else None


def finish_fund_session(user_id: str) -> dict | None:
    """Marca la evaluación como terminada y limpia la pregunta pendiente."""
    return update_fund_session(
        user_id,
        status="evaluated",
        clear_pending_field=True,
    )


def cancel_fund_session(user_id: str) -> dict | None:
    """Cancela la evaluación y limpia la pregunta pendiente."""
    return update_fund_session(
        user_id,
        status="cancelled",
        clear_pending_field=True,
    )


def save_fund_answer(user_id: str, field_key: str, value: Any) -> dict | None:
    """Crea o reemplaza una respuesta del usuario.

    La columna ``value`` es JSONB y no acepta SQL NULL. Una respuesta omitida o
    desconocida se representa explícitamente como ``{"status": "unknown"}``.
    """
    normalized_key = (field_key or "").strip()
    if not normalized_key:
        raise ValueError("field_key no puede estar vacío")

    now = _utc_now_iso()
    payload = {
        "user_id": user_id,
        "field_key": normalized_key,
        "value": UNKNOWN_ANSWER.copy() if value is None else value,
        "updated_at": now,
    }
    result = (
        _admin_client()
        .table("fund_user_answers")
        .upsert(payload, on_conflict="user_id,field_key")
        .execute()
    )
    return result.data[0] if result.data else None


def get_fund_answer_records(user_id: str) -> dict[str, Any]:
    """Obtiene las respuestas JSONB tal como están persistidas."""
    result = (
        _admin_client()
        .table("fund_user_answers")
        .select("field_key,value")
        .eq("user_id", user_id)
        .execute()
    )

    return {
        row["field_key"]: row.get("value")
        for row in (result.data or [])
        if row.get("field_key")
    }


def get_fund_answers(user_id: str) -> dict[str, Any]:
    """Obtiene respuestas listas para evaluar, convirtiendo unknown en None."""
    records = get_fund_answer_records(user_id)
    answers: dict[str, Any] = {}
    for field_key, value in records.items():
        answers[field_key] = None if value == UNKNOWN_ANSWER else value
    return answers


def delete_fund_answer(user_id: str, field_key: str) -> None:
    """Elimina una respuesta para que pueda volver a solicitarse."""
    (
        _admin_client()
        .table("fund_user_answers")
        .delete()
        .eq("user_id", user_id)
        .eq("field_key", field_key)
        .execute()
    )
