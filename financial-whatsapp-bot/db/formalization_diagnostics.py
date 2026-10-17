"""Persistencia transaccional de HDU017 sobre las tablas ya creadas.

Usa el pool PostgreSQL existente (DB_DSN), no nuevas RPC ni varias escrituras
REST independientes. No registra rutas, no llama IA y no calcula capacidad.
Desde async debe invocarse mediante to_thread/threadpool, como db/documents.py.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from schemas.formalization_diagnostics import (
    FormalizationAssessmentInput,
    FormalizationDraftPatch,
    FormalizationItemInput,
)


class FormalizationPersistenceError(RuntimeError):
    """No se pudo acceder al almacenamiento; no equivale a datos vacíos."""


class FormalizationConnectionError(FormalizationPersistenceError):
    """El pool no está disponible o no pudo entregar una conexión."""


class FormalizationConflictError(FormalizationPersistenceError):
    """Borrador, fuentes o solicitud cambiaron: recargar antes de continuar."""


class FormalizationNotFoundError(FormalizationPersistenceError):
    """El registro no existe para el usuario indicado."""


def _get_pool():
    import dependencies

    if dependencies.db_pool is None:
        raise FormalizationConnectionError(
            "El diagnóstico requiere el pool PostgreSQL existente. Revisa DB_DSN; "
            "no se guardará mediante escrituras REST parciales."
        )
    return dependencies.db_pool


@contextmanager
def _transaction(*, readonly: bool = False):
    # Importación diferida: los modelos y utilidades se prueban sin abrir una BD.
    from psycopg2.extras import RealDictCursor

    pool = _get_pool()
    try:
        conn = pool.getconn()
    except Exception as error:
        raise FormalizationConnectionError("No se pudo obtener una conexión PostgreSQL") from error
    previous = (conn.isolation_level, conn.readonly, conn.autocommit)
    discard = False
    try:
        conn.rollback()
        conn.set_session(isolation_level="REPEATABLE READ", readonly=readonly, autocommit=False)
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            yield cursor
        conn.commit()
    except Exception as error:
        try:
            conn.rollback()
        except Exception:
            discard = True
        if getattr(error, "pgcode", None) in {"40001", "40P01", "23505"}:
            raise FormalizationConflictError("La evaluación cambió; recarga el contexto.") from error
        raise
    finally:
        if not discard:
            try:
                conn.set_session(
                    isolation_level=previous[0] if previous[0] is not None else "DEFAULT",
                    readonly=previous[1] if previous[1] is not None else "DEFAULT",
                    autocommit=previous[2],
                )
            except Exception:
                discard = True
        pool.putconn(conn, close=discard)


def _uuid(value: str | UUID) -> str:
    return str(UUID(str(value)))


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (date, UUID)):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    return value


def _dumps(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_dumps(value).encode("utf-8")).hexdigest()


def _today() -> date:
    return datetime.now(ZoneInfo("America/Santiago")).date()


def _validate_period(start: date | None, end: date | None) -> None:
    if (start is None) != (end is None):
        raise ValueError("El periodo requiere inicio y fin")
    if start is not None and not 0 < (end - start).days <= 366:
        raise ValueError("El periodo debe ser semiabierto y abarcar entre 1 y 366 días")


def _read_context(cursor, user_id: str, diagnostic_id: str | None, *, lock: bool = False) -> dict:
    # Subconsulta agregada: cabecera y TODAS sus partidas en una lectura.
    query = """
        SELECT d.*, coalesce((
            SELECT jsonb_agg(
                to_jsonb(i) || jsonb_build_object('amount_original', i.amount_original::text)
                ORDER BY i.item_key
            ) FROM public.formalization_diagnostic_items i WHERE i.diagnostic_id = d.id
        ), '[]'::jsonb) AS _items
        FROM public.formalization_diagnostics d
        WHERE d.user_id = %s AND (%s::uuid IS NULL OR d.id = %s::uuid)
        ORDER BY (d.workflow_status IN ('draft','ready')) DESC, d.updated_at DESC, d.id
        LIMIT 1
    """
    if lock:
        query += " FOR UPDATE OF d"
    cursor.execute(query, (user_id, diagnostic_id, diagnostic_id))
    row = cursor.fetchone()
    if row is None:
        if diagnostic_id:
            raise FormalizationNotFoundError("No se encontró ese diagnóstico para tu perfil")
        return {"diagnostic": None, "items": []}
    row = dict(row)
    return {"diagnostic": row, "items": row.pop("_items")}


_SOURCES_SQL = """
    WITH profile AS (
        SELECT jsonb_build_object(
            'id', id, 'rubro', rubro, 'rubro_raw', rubro_raw, 'comuna', comuna,
            'inicio_sii', inicio_sii, 'roadmap', roadmap,
            'roadmap_completed_at', roadmap_completed_at
        ) AS data FROM public.users WHERE id = %(user_id)s
    ), movements AS MATERIALIZED (
        SELECT id, movement_type, amount, category, currency, description, occurred_on,
               to_char(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS updated_at_utc
        FROM public.financial_movements
        WHERE user_id = %(user_id)s AND status = 'confirmed'
          AND %(start)s::date IS NOT NULL
          AND occurred_on >= %(start)s::date AND occurred_on < %(end)s::date
    ), categories AS (
        SELECT movement_type, category, sum(amount) AS total, count(*) AS count
        FROM movements GROUP BY movement_type, category
    )
    SELECT p.data AS profile,
        CASE WHEN %(start)s::date IS NULL THEN '{}'::jsonb ELSE jsonb_build_object(
            'loaded', true, 'period_start', %(start)s::date, 'period_end', %(end)s::date,
            'currency', 'CLP', 'coverage_confirmed', false,
            'movement_count', (SELECT count(*) FROM movements),
            'first_movement_on', (SELECT min(occurred_on) FROM movements),
            'last_movement_on', (SELECT max(occurred_on) FROM movements),
            'income_total', (SELECT coalesce(sum(amount),0) FROM movements WHERE movement_type = 'income'),
            'operating_income_total', (SELECT coalesce(sum(amount),0) FROM movements
                WHERE movement_type = 'income' AND category IN ('ventas','servicios')),
            'capital_contributions_total', (SELECT coalesce(sum(amount),0) FROM movements
                WHERE movement_type = 'income' AND category = 'aportes_capital'),
            'other_income_total', (SELECT coalesce(sum(amount),0) FROM movements
                WHERE movement_type = 'income' AND category = 'otros_ingresos'),
            'expense_total', (SELECT coalesce(sum(amount),0) FROM movements WHERE movement_type = 'expense'),
            'categories', (SELECT coalesce(jsonb_agg(to_jsonb(c) ORDER BY movement_type,category),'[]'::jsonb) FROM categories c),
            'needs_review', jsonb_build_array('otros_ingresos','arriendo_servicios','equipamiento')
        ) END AS financial,
        (SELECT encode(sha256(convert_to(coalesce(jsonb_agg(to_jsonb(m) ORDER BY id),'[]'::jsonb)::text,'UTF8')),'hex')
            FROM movements m) AS movements_hash,
        (SELECT coalesce(jsonb_agg(
            (to_jsonb(c) - 'created_at') || jsonb_build_object(
                'amount_min',c.amount_min::text,'amount_max',c.amount_max::text)
            ORDER BY c.code,c.version,c.id),'[]'::jsonb)
            FROM public.get_formalization_cost_candidates(
                %(activity)s,%(comuna)s,%(location)s,%(as_of)s) c) AS catalog,
        statement_timestamp() AS source_checked_at
    FROM profile p
"""


def _load_sources(cursor, user_id: str, diagnostic: dict, as_of: date) -> dict:
    start, end = diagnostic.get("period_start"), diagnostic.get("period_end")
    _validate_period(start, end)
    scope = {key: diagnostic.get(key) for key in ("activity_code", "comuna_code", "location_type")}
    cursor.execute(_SOURCES_SQL, {
        "user_id": user_id, "start": start, "end": end, "as_of": as_of,
        "activity": scope["activity_code"], "comuna": scope["comuna_code"], "location": scope["location_type"],
    })
    row = cursor.fetchone()
    if row is None:
        raise FormalizationNotFoundError("No se encontró el perfil")
    return {
        "profile_snapshot": row["profile"], "financial_snapshot": row["financial"],
        "source_fingerprint": _hash({
            "profile": row["profile"], "period_start": start, "period_end": end,
            "movements_hash": row["movements_hash"],
        }),
        "catalog_fingerprint": _hash({"scope": scope, "catalog": row["catalog"]}),
        "catalog": row["catalog"], "source_checked_at": row["source_checked_at"],
    }


def get_formalization_context(
    user_id: str, diagnostic_id: str | None = None, *, refresh_sources: bool = False,
    period_start: date | None = None, period_end: date | None = None, as_of: date | None = None,
) -> dict:
    """Lee borrador/partidas. Solo refresca perfil, ledger y catálogo si se solicita.

    El refresco se devuelve aparte; no sobrescribe lo confirmado ni guarda nada.
    Para un diagnóstico nuevo se puede indicar el periodo antes de crearlo.
    """
    user_id = _uuid(user_id)
    diagnostic_id = _uuid(diagnostic_id) if diagnostic_id else None
    if period_start is not None or period_end is not None:
        _validate_period(period_start, period_end)
        if not refresh_sources:
            raise ValueError("El periodo alternativo requiere refresh_sources=True")
    with _transaction(readonly=True) as cursor:
        context = _read_context(cursor, user_id, diagnostic_id)
        if refresh_sources:
            diagnostic = dict(context["diagnostic"] or {})
            if period_start is not None:
                diagnostic.update(period_start=period_start, period_end=period_end)
            context["fresh_sources"] = _load_sources(cursor, user_id, diagnostic, as_of or _today())
        return _jsonable(context)


_JSON_HEADER_FIELDS = {
    "answers", "rules_config", "missing_fields", "profile_snapshot", "financial_snapshot", "result",
}
_HEADER_FIELDS = set(FormalizationDraftPatch.model_fields) | {
    "profile_snapshot", "financial_snapshot", "source_fingerprint", "catalog_fingerprint",
    "result", "assessment_status", "capacity", "calculated_at", "confirmed_at", "latest_revision_id",
}


def _update_header(cursor, diagnostic_id: str, user_id: str, values: dict) -> dict:
    if not values or not set(values) <= _HEADER_FIELDS:
        raise ValueError("Campos de cabecera no permitidos")
    # Identificadores proceden exclusivamente de la lista cerrada anterior.
    assignments, parameters = [], []
    for field, value in values.items():
        assignments.append(field + (" = %s::jsonb" if field in _JSON_HEADER_FIELDS else " = %s"))
        if field in _JSON_HEADER_FIELDS and value is not None:
            parameters.append(_dumps(value))
        else:
            parameters.append(str(value) if isinstance(value, UUID) else value)
    cursor.execute(
        "UPDATE public.formalization_diagnostics SET " + ", ".join(assignments)
        + " WHERE id = %s AND user_id = %s RETURNING *",
        (*parameters, diagnostic_id, user_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise FormalizationNotFoundError("No se encontró ese diagnóstico para tu perfil")
    return dict(row)


def _prepare_items(diagnostic_id: str, existing: list[dict], inputs: list[FormalizationItemInput],
                   deleted: set[str], catalog: dict[str, dict]) -> list[dict]:
    by_key = {row["item_key"]: row for row in existing if row["item_key"] not in deleted}
    ids = {key: str(row["id"]) for key, row in by_key.items()}
    ids.update({item.item_key: ids.get(item.item_key) or str(uuid5(
        NAMESPACE_URL, f"financial:formalization-item:{diagnostic_id}:{item.item_key}"
    )) for item in inputs})
    graph = {str(row["id"]): str(row["replaces_item_id"]) if row.get("replaces_item_id") else None
             for row in by_key.values()}
    payloads = {}
    for item in inputs:
        row = item.model_dump(mode="python", exclude={"replaces_item_key"})
        row.update(id=ids[item.item_key], diagnostic_id=diagnostic_id)
        target = item.replaces_item_key
        if target is not None and target not in ids:
            raise ValueError("La partida sustituida debe existir en el mismo diagnóstico")
        row["replaces_item_id"] = ids[target] if target else None
        previous = by_key.get(item.item_key, {})
        catalog_id = item.catalog_version_id
        if catalog_id:
            if str(previous.get("catalog_version_id")) == catalog_id:
                row["catalog_snapshot"] = previous["catalog_snapshot"]
            else:
                if catalog_id not in catalog or not catalog[catalog_id]["active"]:
                    raise ValueError("La versión de catálogo no existe o no está activa")
                row["catalog_snapshot"] = catalog[catalog_id]
        else:
            row["catalog_snapshot"] = {}
        payloads[row["id"]] = row
        graph[row["id"]] = row["replaces_item_id"]
    if any(target and target not in graph for target in graph.values()):
        raise ValueError("No puedes borrar una partida que todavía está siendo sustituida")
    # Ordenar dependencias antes de escribir evita ciclos transitorios del trigger.
    ordered, visited = [], set()
    for node in graph:
        trail, visiting = [], set()
        while node is not None and node not in visited:
            if node in visiting:
                raise ValueError("Las sustituciones no pueden formar ciclos")
            visiting.add(node)
            trail.append(node)
            node = graph[node]
        for node in reversed(trail):
            visited.add(node)
            if node in payloads:
                ordered.append(payloads[node])
    return ordered


_ITEM_TYPES = (
    ("id", "uuid"), ("diagnostic_id", "uuid"), ("item_key", "text"), ("catalog_version_id", "uuid"),
    ("item_kind", "text"), ("economic_nature", "text"), ("cash_direction", "text"), ("label", "text"),
    ("applicability", "text"), ("applicability_reason", "text"), ("amount_original", "numeric"), ("unit", "text"),
    ("amount_clp_min", "bigint"), ("amount_clp_max", "bigint"), ("amount_paid_clp", "bigint"),
    ("amount_state", "text"), ("source_type", "text"), ("frequency", "text"), ("due_date", "date"),
    ("schedule", "jsonb"), ("replaces_item_id", "uuid"), ("included_in_baseline", "boolean"),
    ("catalog_snapshot", "jsonb"), ("evidence", "jsonb"), ("metadata", "jsonb"),
)


def _upsert_items(cursor, rows: list[dict]) -> None:
    if not rows:
        return
    columns = ",".join(name for name, _ in _ITEM_TYPES)
    definitions = ",".join(name + " " + sql_type for name, sql_type in _ITEM_TYPES)
    updates = ",".join(name + " = EXCLUDED." + name for name, _ in _ITEM_TYPES
                       if name not in {"id", "diagnostic_id", "item_key"})
    cursor.execute(
        "INSERT INTO public.formalization_diagnostic_items (" + columns + ") SELECT "
        + ",".join("r." + name for name, _ in _ITEM_TYPES)
        + " FROM jsonb_array_elements(%s::jsonb) WITH ORDINALITY a(payload,position)"
        + " CROSS JOIN LATERAL jsonb_to_record(a.payload) AS r(" + definitions + ")"
        + " ORDER BY a.position ON CONFLICT (diagnostic_id,item_key) DO UPDATE SET " + updates,
        (_dumps(rows),),
    )


def save_formalization_draft(
    user_id: str, diagnostic_id: str, expected_lock_version: int,
    patch: FormalizationDraftPatch | dict | None = None, *,
    items: list[FormalizationItemInput | dict] | None = None, delete_item_keys: list[str] | None = None,
    refresh_sources: bool = False, reopen: bool = False, as_of: date | None = None,
) -> dict:
    """Guarda entradas y lote de partidas en una transacción, con control de versión.

    Crear requiere UUID del backend y expected_lock_version=0. Un reintento
    obsoleto devuelve conflicto, nunca suma ni sobreescribe una respuesta nueva.
    """
    user_id, diagnostic_id = _uuid(user_id), _uuid(diagnostic_id)
    if type(expected_lock_version) is not int or expected_lock_version < 0:
        raise ValueError("Versión esperada inválida")
    patch = patch if isinstance(patch, FormalizationDraftPatch) else FormalizationDraftPatch.model_validate(patch or {})
    values = patch.model_dump(exclude_unset=True)
    inputs = [item if isinstance(item, FormalizationItemInput) else FormalizationItemInput.model_validate(item)
              for item in (items or [])]
    if len(inputs) > 200 or len(delete_item_keys or []) > 200:
        raise ValueError("Máximo 200 cambios por lote")
    keys = [item.item_key for item in inputs]
    deleted = set(delete_item_keys or [])
    if any(not isinstance(key, str) or not key.strip() or len(key) > 120 for key in deleted):
        raise ValueError("Clave de partida inválida")
    if len(set(keys)) != len(keys) or deleted.intersection(keys):
        raise ValueError("Una clave no puede repetirse ni eliminarse y guardarse en el mismo lote")
    _dumps(values)
    day = as_of or _today()
    with _transaction() as cursor:
        try:
            context = _read_context(cursor, user_id, diagnostic_id, lock=True)
            current = context["diagnostic"]
            created = False
        except FormalizationNotFoundError:
            created = True
        if created:
            if expected_lock_version != 0:
                raise FormalizationNotFoundError("El diagnóstico no existe")
            cursor.execute(
                "INSERT INTO public.formalization_diagnostics (id,user_id) VALUES (%s,%s)"
                " ON CONFLICT (id) DO NOTHING RETURNING *", (diagnostic_id, user_id),
            )
            current = cursor.fetchone()
            if current is None:
                raise FormalizationConflictError("El diagnóstico ya fue creado; recarga el contexto")
            context = {"diagnostic": dict(current), "items": []}
        current = dict(current)
        if str(current["user_id"]) != user_id:
            raise FormalizationNotFoundError("No se encontró ese diagnóstico para tu perfil")
        if current["lock_version"] != expected_lock_version:
            raise FormalizationConflictError("El borrador cambió; recarga antes de guardar")
        if current["workflow_status"] in {"cancelled", "archived"}:
            raise FormalizationConflictError("El diagnóstico está cerrado")
        if current["workflow_status"] == "confirmed" and not reopen:
            raise FormalizationConflictError("Debes reabrir explícitamente la evaluación")
        merged = {**current, **values}
        _validate_period(merged.get("period_start"), merged.get("period_end"))
        scope_changed = any(current.get(key) != merged.get(key) for key in (
            "period_start", "period_end", "activity_code", "comuna_code", "location_type",
        ))
        requested_status = values.pop("workflow_status", "draft")
        values["workflow_status"] = "draft"
        if current["workflow_status"] == "confirmed":
            values.setdefault("conversation_status", "paused")
            values.update(result=None, capacity=None, assessment_status="partial",
                          calculated_at=None, confirmed_at=None)
        current = _update_header(cursor, diagnostic_id, user_id, values)
        sources = None
        if created or refresh_sources or scope_changed:
            sources = _load_sources(cursor, user_id, current, day)
            current = _update_header(cursor, diagnostic_id, user_id, {
                key: sources[key] for key in (
                    "profile_snapshot", "financial_snapshot", "source_fingerprint", "catalog_fingerprint",
                )
            })
        existing_catalog = {row["item_key"]: str(row.get("catalog_version_id")) for row in context["items"]}
        catalog_ids = sorted({_uuid(item.catalog_version_id) for item in inputs if item.catalog_version_id
                              and existing_catalog.get(item.item_key) != item.catalog_version_id})
        catalog = {}
        if catalog_ids:
            cursor.execute("SELECT * FROM public.formalization_cost_catalog WHERE id = ANY(%s::uuid[])", (catalog_ids,))
            catalog = {str(row["id"]): _jsonable(dict(row)) for row in cursor.fetchall()}
        prepared = _prepare_items(diagnostic_id, context["items"], inputs, deleted, catalog)
        if deleted:
            cursor.execute(
                "DELETE FROM public.formalization_diagnostic_items WHERE diagnostic_id = %s AND item_key = ANY(%s::text[])",
                (diagnostic_id, sorted(deleted)),
            )
        _upsert_items(cursor, prepared)
        if prepared or deleted:
            # Los triggers cambian la versión de cabecera al modificar partidas.
            context = _read_context(cursor, user_id, diagnostic_id)
            current = context["diagnostic"]
        if requested_status != "draft":
            final = {"workflow_status": requested_status}
            if requested_status in {"cancelled", "archived"}:
                final.update(conversation_status="closed", pending_field=None)
            current = _update_header(cursor, diagnostic_id, user_id, final)
        result = {"diagnostic": current, "items": context["items"]}
        if sources is not None:
            result["fresh_sources"] = sources
        return _jsonable(result)


def _validate_complete(diagnostic: dict, items: list[dict], sources: dict) -> None:
    required = ("period_start", "period_end", "projection_start", "activity_code", "comuna_code", "location_type")
    if any(diagnostic.get(key) is None for key in required):
        raise ValueError("Faltan contexto, periodo o fecha de proyección para clasificar")
    if not items:
        raise ValueError("No puedes clasificar un diagnóstico sin partidas revisadas")
    catalog = {str(row["id"]): row for row in sources["catalog"]}
    for item in items:
        if item["applicability"] == "to_confirm":
            raise ValueError("Hay partidas cuya aplicabilidad sigue pendiente")
        if item["applicability"] != "applies":
            continue
        if item["amount_state"] == "pending" or item["amount_clp_min"] is None or item["amount_clp_max"] is None:
            raise ValueError("Hay costos aplicables sin un importe acotado")
        catalog_id = item.get("catalog_version_id")
        if catalog_id:
            concept = catalog.get(str(catalog_id))
            if concept is None:
                raise FormalizationConflictError("Una partida utiliza un catálogo retirado o fuera de alcance")
            if concept["pricing_method"] != "quote" and concept["review_status"] != "reviewed":
                raise ValueError("Una regla obligatoria o tarifaria todavía requiere revisión")
        if item["amount_state"] in {"estimated", "quoted"} and not item["evidence"]:
            raise ValueError("Las estimaciones/cotizaciones necesitan respaldo y supuestos")


def confirm_formalization_revision(
    user_id: str, diagnostic_id: str, expected_lock_version: int, request_id: str,
    assessment: FormalizationAssessmentInput | dict, *, as_of: date | None = None,
) -> dict:
    """Confirma resultado + snapshot atómicamente y resuelve reintentos idénticos.

    Comprueba fuentes en un corte coherente REPEATABLE READ, no promete que
    no aparezcan movimientos posteriores. La aritmética se valida antes en el
    motor determinista; esta función comprueba persistencia y vigencia del corte.
    """
    user_id, diagnostic_id, request_id = _uuid(user_id), _uuid(diagnostic_id), _uuid(request_id)
    if type(expected_lock_version) is not int or expected_lock_version < 0:
        raise ValueError("Versión esperada inválida")
    assessment = assessment if isinstance(assessment, FormalizationAssessmentInput) else FormalizationAssessmentInput.model_validate(assessment)
    payload = assessment.model_dump(mode="python")
    request_hash = _hash({"user_id": user_id, "diagnostic_id": diagnostic_id,
                          "lock_version": expected_lock_version, "assessment": payload})
    if len(_dumps(payload).encode("utf-8")) > 512_000:
        raise ValueError("Resultado demasiado grande")
    with _transaction() as cursor:
        context = _read_context(cursor, user_id, diagnostic_id, lock=True)
        diagnostic = context["diagnostic"]
        cursor.execute(
            "SELECT * FROM public.formalization_diagnostic_revisions WHERE diagnostic_id = %s AND request_id = %s",
            (diagnostic_id, request_id),
        )
        existing = cursor.fetchone()
        if existing is not None:
            if existing["request_hash"] != request_hash:
                raise FormalizationConflictError("La solicitud ya se usó con otro contenido")
            return _jsonable({"revision": dict(existing), "replayed": True})
        if diagnostic["lock_version"] != expected_lock_version:
            raise FormalizationConflictError("El borrador cambió; vuelve a calcular")
        if diagnostic["workflow_status"] not in {"draft", "ready"}:
            raise FormalizationConflictError("El diagnóstico no está abierto")
        if not diagnostic.get("rules_version"):
            raise ValueError("Debes guardar la versión de reglas antes de confirmar")
        sources = _load_sources(cursor, user_id, diagnostic, as_of or _today())
        if any(diagnostic.get(key) != sources[key] for key in ("source_fingerprint", "catalog_fingerprint")):
            raise FormalizationConflictError("Cambió el perfil, los movimientos o el catálogo; revisa y recalcula")
        if assessment.assessment_status == "complete":
            _validate_complete(diagnostic, context["items"], sources)
        now = datetime.now(timezone.utc)
        snapshot = {
            "diagnostic": {key: diagnostic.get(key) for key in (
                "id", "user_id", "period_start", "period_end", "projection_start", "horizon_months",
                "activity_code", "comuna_code", "location_type", "answers", "profile_snapshot", "financial_snapshot",
                "rules_version", "rules_config", "technical_status", "source_fingerprint", "catalog_fingerprint",
            )},
            "assessment": payload, "items": context["items"], "catalog": sources["catalog"],
            "source_checked_at": sources["source_checked_at"], "confirmed_at": now,
        }
        cursor.execute(
            "INSERT INTO public.formalization_diagnostic_revisions "
            "(diagnostic_id,revision_number,request_id,request_hash,snapshot) "
            "VALUES (%s,NULL,%s,%s,%s::jsonb) RETURNING *",
            (diagnostic_id, request_id, request_hash, _dumps(snapshot)),
        )
        revision = dict(cursor.fetchone())  # El trigger asigna número y snapshot_hash.
        saved = _update_header(cursor, diagnostic_id, user_id, {
            **payload, "workflow_status": "confirmed", "conversation_status": "closed",
            "current_step": "result", "pending_field": None, "latest_revision_id": revision["id"],
            "calculated_at": now, "confirmed_at": now,
        })
        return _jsonable({"diagnostic": saved, "revision": revision, "replayed": False})
