"""Acceso a `community_events` y `community_event_deliveries` (HdU019).

Usa el cliente administrativo (service role): las tablas no tienen
políticas RLS. Todas las operaciones del panel filtran por la comuna de la
cuenta admin, igual que db/expert_requests.py, para que un municipio nunca
lea ni modifique convocatorias de otro.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any


logger = logging.getLogger("financial")

VALID_DELIVERY_STATUSES = {
    "pending",
    "sent",
    "delivered",
    "read",
    "failed",
    "cancelled",
}

_DELIVERY_STATUS_RANK = {
    "pending": 0,
    "sent": 1,
    "delivered": 2,
    "read": 3,
    "failed": 4,
    "cancelled": 5,
}

# Entregas que impiden volver a enviar el aviso al mismo usuario.
_BLOCKING_DELIVERY_STATUSES = ("pending", "sent", "delivered", "read")

# Un número que falla siempre (por ejemplo, inválido) no debe reintentarse
# en cada ciclo del cron hasta la fecha de la actividad.
MAX_DELIVERY_ATTEMPTS = 3

# PostgREST corta las respuestas en 1000 filas por defecto.
_PAGE_SIZE = 1000

# La réplica en Google deja de reintentarse para actividades ya pasadas.
_GOOGLE_RETRY_WINDOW = timedelta(days=7)


def _admin_client():
    import dependencies

    if dependencies.supabase_admin is None:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY no está configurada; no se pueden "
            "utilizar las convocatorias comunitarias"
        )
    return dependencies.supabase_admin


def _optional_admin_client():
    import dependencies

    return dependencies.supabase_admin


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _event_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Columnas editables a partir de los datos ya validados."""
    return {
        "title": data["title"],
        "category": data["category"],
        "description": data["description"],
        "location": data.get("location"),
        "link": data.get("link"),
        "event_at": _iso_utc(data["event_at"]),
        "notice_at": _iso_utc(data["notice_at"]),
    }


# ============================================================
# CONVOCATORIAS (panel admin y calendario del usuario)
# ============================================================

def create_community_event(
    comuna: str,
    created_by: str,
    data: dict[str, Any],
) -> dict | None:
    """Crea una convocatoria ya validada por core/community_events.py."""
    payload = _event_payload(data)
    payload.update({
        "comuna": comuna,
        "created_by": created_by,
        "status": "scheduled",
        "google_sync_status": "pending",
    })
    result = _admin_client().table("community_events").insert(payload).execute()
    return result.data[0] if result.data else None


def get_community_event(event_id: str, comuna: str) -> dict | None:
    """Trae una convocatoria solo si pertenece a la comuna indicada."""
    result = (
        _admin_client()
        .table("community_events")
        .select("*")
        .eq("id", event_id)
        .eq("comuna", comuna)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def list_community_events_between(
    comuna: str,
    start_at: datetime,
    end_at: datetime,
    *,
    include_cancelled: bool = False,
) -> list[dict]:
    """Convocatorias de una comuna en un rango semiabierto [inicio, fin).

    El admin ve también las canceladas; el calendario del usuario no.
    """
    if not comuna:
        return []

    query = (
        _admin_client()
        .table("community_events")
        .select("*")
        .eq("comuna", comuna)
        .gte("event_at", _iso_utc(start_at))
        .lt("event_at", _iso_utc(end_at))
    )
    if not include_cancelled:
        query = query.eq("status", "scheduled")

    result = query.order("event_at", desc=False).execute()
    return result.data or []


def list_community_events_since(comuna: str, since: datetime) -> list[dict]:
    """Convocatorias (incluidas las canceladas) desde una fecha, para el .ics."""
    result = (
        _admin_client()
        .table("community_events")
        .select("*")
        .eq("comuna", comuna)
        .gte("event_at", _iso_utc(since))
        .order("event_at", desc=False)
        .execute()
    )
    return result.data or []


def update_community_event(
    event_id: str,
    comuna: str,
    data: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict | None:
    """Actualiza una convocatoria cuyo aviso todavía no llega.

    Devuelve None si no existe, si es de otra comuna, si fue cancelada o si
    la hora del aviso ya pasó (el aviso pudo haber empezado a enviarse).
    """
    current = now or _utc_now()
    payload = _event_payload(data)
    payload["google_sync_status"] = "pending"

    result = (
        _admin_client()
        .table("community_events")
        .update(payload)
        .eq("id", event_id)
        .eq("comuna", comuna)
        .eq("status", "scheduled")
        .is_("notice_sent_at", "null")
        .gt("notice_at", _iso_utc(current))
        .execute()
    )
    return result.data[0] if result.data else None


def cancel_community_event(
    event_id: str,
    comuna: str,
    *,
    now: datetime | None = None,
) -> dict | None:
    """Cancela una convocatoria cuya actividad todavía no ocurre.

    Si el aviso no había salido, ya no se envía: se cancelan las entregas
    reservadas y la convocatoria deja de aparecer como vencida.
    """
    current = now or _utc_now()
    result = (
        _admin_client()
        .table("community_events")
        .update({
            "status": "cancelled",
            "cancelled_at": _iso_utc(current),
            "google_sync_status": "pending",
        })
        .eq("id", event_id)
        .eq("comuna", comuna)
        .eq("status", "scheduled")
        .gt("event_at", _iso_utc(current))
        .execute()
    )
    if not result.data:
        return None

    cancel_pending_community_deliveries(event_id)
    return result.data[0]


def get_delivery_summary(event_ids: list[str]) -> dict[str, dict[str, int]]:
    """Conteo de entregas por convocatoria: enviadas, fallidas y pendientes.

    «Enviadas» agrupa sent/delivered/read: para el admin lo relevante es que
    Meta aceptó el mensaje.
    """
    summary = {
        event_id: {"sent": 0, "failed": 0, "pending": 0}
        for event_id in event_ids
    }
    if not event_ids:
        return summary

    rows = _fetch_all(
        lambda: (
            _admin_client()
            .table("community_event_deliveries")
            .select("event_id,delivery_status")
            .in_("event_id", event_ids)
            .order("id")
        )
    )
    for row in rows:
        counts = summary.get(row.get("event_id"))
        if counts is None:
            continue
        status = row.get("delivery_status")
        if status in ("sent", "delivered", "read"):
            counts["sent"] += 1
        elif status == "failed":
            counts["failed"] += 1
        elif status == "pending":
            counts["pending"] += 1
    return summary


def _recipients_query(comuna: str, columns: str, **select_kwargs):
    """Usuarios que deben recibir el aviso: misma comuna, recordatorios
    activados, no bloqueados y con el onboarding terminado."""
    return (
        _admin_client()
        .table("users")
        .select(columns, **select_kwargs)
        .eq("comuna", comuna)
        .eq("reminders_enabled", True)
        .eq("bloqueado", False)
        .eq("onboarding_step", "done")
        .not_.is_("phone", "null")
    )


def count_notice_recipients(comuna: str) -> int:
    """Cuántos usuarios recibirían hoy el aviso de una convocatoria."""
    try:
        result = _recipients_query(comuna, "id", count="exact").limit(1).execute()
        return int(result.count or 0)
    except Exception as error:
        logger.error("No se pudo contar los destinatarios de %s: %s", comuna, error)
        return 0


def _fetch_all(build_query) -> list[dict]:
    """Recorre una consulta en páginas de _PAGE_SIZE filas."""
    rows: list[dict] = []
    offset = 0
    while True:
        page = build_query().range(offset, offset + _PAGE_SIZE - 1).execute().data or []
        rows.extend(page)
        if len(page) < _PAGE_SIZE:
            return rows
        offset += _PAGE_SIZE


# ============================================================
# ENVÍO DEL AVISO
# ============================================================

def get_due_community_events(
    limit: int,
    now: datetime | None = None,
) -> list[dict]:
    """Convocatorias cuyo aviso ya debe enviarse.

    Se excluyen las actividades ya ocurridas: si el worker estuvo caído, no
    se avisa de algo que ya pasó.
    """
    if limit < 1:
        raise ValueError("El límite debe ser mayor que cero")

    current_iso = _iso_utc(now or _utc_now())
    result = (
        _admin_client()
        .table("community_events")
        .select("*")
        .eq("status", "scheduled")
        .is_("notice_sent_at", "null")
        .lte("notice_at", current_iso)
        .gt("event_at", current_iso)
        .order("notice_at", desc=False)
        .limit(limit)
        .execute()
    )
    return result.data or []


def get_pending_recipients(event: dict, limit: int) -> list[dict]:
    """Usuarios de la comuna que todavía no tienen el aviso de esta convocatoria.

    Los destinatarios se calculan al momento del envío: quien activó o
    desactivó los recordatorios después de crear la convocatoria queda bien
    filtrado. Las entregas fallidas se reintentan hasta MAX_DELIVERY_ATTEMPTS.
    """
    if limit < 1:
        raise ValueError("El límite debe ser mayor que cero")

    event_id = event["id"]
    deliveries = _fetch_all(
        lambda: (
            _admin_client()
            .table("community_event_deliveries")
            .select("user_id,delivery_status,attempt_count")
            .eq("event_id", event_id)
            .order("id")
        )
    )
    excluded = {
        row.get("user_id")
        for row in deliveries
        if row.get("delivery_status") in _BLOCKING_DELIVERY_STATUSES
        or row.get("delivery_status") == "cancelled"
        or (
            row.get("delivery_status") == "failed"
            and int(row.get("attempt_count") or 0) >= MAX_DELIVERY_ATTEMPTS
        )
    }

    recipients: list[dict] = []
    offset = 0
    while len(recipients) < limit:
        page = (
            _recipients_query(event["comuna"], "id,phone")
            .order("id")
            .range(offset, offset + _PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        for user in page:
            if user.get("id") in excluded or not user.get("phone"):
                continue
            recipients.append({"user_id": user["id"], "phone": user["phone"]})
            if len(recipients) >= limit:
                break
        if len(page) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE

    return recipients


def reserve_community_delivery(event_id: str, user_id: str) -> str | None:
    """Reserva el envío del aviso para un usuario.

    La restricción única (event_id, user_id) impide que dos ejecuciones del
    cron envíen el mismo aviso. Un intento fallido puede reutilizarse.
    """
    client = _admin_client()
    try:
        result = (
            client
            .table("community_event_deliveries")
            .insert({
                "event_id": event_id,
                "user_id": user_id,
                "delivery_status": "pending",
                "attempt_count": 1,
            })
            .execute()
        )
        return result.data[0]["id"] if result.data else None

    except Exception as error:
        try:
            existing = (
                client
                .table("community_event_deliveries")
                .select("id,delivery_status,attempt_count")
                .eq("event_id", event_id)
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            if not existing.data:
                logger.error(
                    "No se pudo reservar el aviso de la convocatoria %s: %s",
                    event_id,
                    error,
                )
                return None

            delivery = existing.data[0]
            if delivery.get("delivery_status") != "failed":
                return None

            attempt_count = int(delivery.get("attempt_count") or 0)
            if attempt_count >= MAX_DELIVERY_ATTEMPTS:
                return None

            retry = (
                client
                .table("community_event_deliveries")
                .update({
                    "delivery_status": "pending",
                    "provider_message_id": None,
                    "failure_reason": None,
                    "attempt_count": attempt_count + 1,
                    "sent_at": None,
                    "delivered_at": None,
                    "read_at": None,
                })
                .eq("id", delivery["id"])
                .eq("delivery_status", "failed")
                .execute()
            )
            return delivery["id"] if retry.data else None

        except Exception as lookup_error:
            logger.error(
                "No se pudo consultar el aviso de la convocatoria %s: %s",
                event_id,
                lookup_error,
            )
            return None


def mark_community_delivery_sent(delivery_id: str, provider_message_id: str) -> None:
    """Registra que Meta aceptó el envío."""
    (
        _admin_client()
        .table("community_event_deliveries")
        .update({
            "delivery_status": "sent",
            "provider_message_id": provider_message_id,
            "sent_at": _iso_utc(_utc_now()),
            "failure_reason": None,
        })
        .eq("id", delivery_id)
        .execute()
    )


def mark_community_delivery_failed(delivery_id: str, reason: str) -> None:
    """Registra un intento fallido."""
    (
        _admin_client()
        .table("community_event_deliveries")
        .update({
            "delivery_status": "failed",
            "failure_reason": (reason or "Error desconocido")[:2000],
        })
        .eq("id", delivery_id)
        .execute()
    )


def mark_community_event_notified(event_id: str, now: datetime | None = None) -> None:
    """Marca que el aviso terminó de despacharse a todos los destinatarios."""
    (
        _admin_client()
        .table("community_events")
        .update({"notice_sent_at": _iso_utc(now or _utc_now())})
        .eq("id", event_id)
        .is_("notice_sent_at", "null")
        .execute()
    )


def cancel_pending_community_deliveries(event_id: str) -> None:
    """Cancela las reservas pendientes cuando la convocatoria se cancela."""
    client = _optional_admin_client()
    if client is None:
        return
    try:
        (
            client
            .table("community_event_deliveries")
            .update({"delivery_status": "cancelled"})
            .eq("event_id", event_id)
            .eq("delivery_status", "pending")
            .execute()
        )
    except Exception as error:
        logger.error(
            "No se pudieron cancelar los avisos pendientes de la convocatoria %s: %s",
            event_id,
            error,
        )


def update_community_delivery_status(
    provider_message_id: str,
    status: str,
    event_timestamp: str | int | None = None,
    failure_reason: str | None = None,
) -> bool:
    """Actualiza un aviso usando los webhooks de estado de Meta.

    Retorna True si provider_message_id pertenecía a un aviso de convocatoria.
    """
    if not provider_message_id or status not in VALID_DELIVERY_STATUSES:
        return False

    client = _optional_admin_client()
    if client is None:
        return False

    existing = (
        client
        .table("community_event_deliveries")
        .select("id,delivery_status")
        .eq("provider_message_id", provider_message_id)
        .limit(1)
        .execute()
    )
    if not existing.data:
        return False

    delivery = existing.data[0]
    current_status = delivery.get("delivery_status", "pending")

    # Ignora actualizaciones atrasadas (por ejemplo, "delivered" después de "read").
    if (
        status != "failed"
        and _DELIVERY_STATUS_RANK.get(status, 0)
        < _DELIVERY_STATUS_RANK.get(current_status, 0)
    ):
        return True

    try:
        occurred_at = (
            datetime.fromtimestamp(int(event_timestamp), tz=timezone.utc)
            if event_timestamp
            else _utc_now()
        )
    except (TypeError, ValueError, OSError):
        occurred_at = _utc_now()

    changes: dict[str, Any] = {"delivery_status": status}
    timestamp_column = {
        "sent": "sent_at",
        "delivered": "delivered_at",
        "read": "read_at",
    }.get(status)
    if timestamp_column:
        changes[timestamp_column] = _iso_utc(occurred_at)
    if status == "failed":
        changes["failure_reason"] = (
            failure_reason or "Meta informó un envío fallido"
        )[:2000]

    (
        client
        .table("community_event_deliveries")
        .update(changes)
        .eq("id", delivery["id"])
        .execute()
    )
    return True


# ============================================================
# RÉPLICA EN GOOGLE CALENDAR
# ============================================================

def list_events_pending_google_sync(
    limit: int,
    now: datetime | None = None,
) -> list[dict]:
    """Convocatorias cuya réplica en Google quedó pendiente o falló."""
    since = (now or _utc_now()) - _GOOGLE_RETRY_WINDOW
    result = (
        _admin_client()
        .table("community_events")
        .select("*")
        .in_("google_sync_status", ["pending", "failed"])
        .gte("event_at", _iso_utc(since))
        .order("updated_at", desc=False)
        .limit(limit)
        .execute()
    )
    return result.data or []


def set_google_sync_result(
    event_id: str,
    status: str,
    error: str | None = None,
) -> None:
    """Guarda el resultado de la réplica en Google Calendar."""
    changes: dict[str, Any] = {
        "google_sync_status": status,
        "google_sync_error": (error or None) and error[:2000],
    }
    if status == "synced":
        changes["google_synced_at"] = _iso_utc(_utc_now())
    (
        _admin_client()
        .table("community_events")
        .update(changes)
        .eq("id", event_id)
        .execute()
    )
