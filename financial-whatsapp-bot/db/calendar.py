from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger("financial")

# ============================================================
# ESTADOS PERMITIDOS
# ============================================================

VALID_EVENT_STATUSES = {
    "active",
    "completed",
    "cancelled",
}

VALID_SESSION_STATES = {
    "waiting_date",
    "waiting_description",
    "confirming_creation",
    "waiting_new_date",
    "confirming_update",
    "confirming_delete",
}

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
    "cancelled": 5
}

_UNSET = object()



def _admin_client():
    import dependencies

    if dependencies.supabase_admin is None:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY no esta configurada; no se puede utilizar el calendario personalizado")
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

def _timestamp_key(value:str | datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return _iso_utc(value)
    normalized= str(value).strip()
    if not normalized:
        return ""
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        return _iso_utc(parsed)
    except ValueError:
        return normalized

def _normalize_description(description: str) -> str:
    """Valida y normaliza la descripción de un evento."""
    normalized = (description or "").strip()

    if not normalized:
        raise ValueError("La descripción del evento no puede estar vacía")

    if len(normalized) > 500:
        raise ValueError(
            "La descripción del evento no puede superar los 500 caracteres"
        )

    return normalized


# ============================================================
# EVENTOS
# ============================================================

def create_calendar_event(
    user_id: str,
    description: str,
    event_at: datetime,
    reminder_at: datetime,
) -> dict | None:
    """Crea un evento confirmado para un usuario.

    La validación de que la fecha sea futura debe realizarse primero en
    core/calendar_flow.py. Esta función valida solamente los datos mínimos
    necesarios para persistir el registro.
    """
    normalized_description = _normalize_description(description)

    if reminder_at > event_at:
        raise ValueError(
            "La fecha del recordatorio no puede ser posterior al evento"
        )

    payload = {
        "user_id": user_id,
        "description": normalized_description,
        "event_at": _iso_utc(event_at),
        "reminder_at": _iso_utc(reminder_at),
        "status": "active",
    }

    result = (
        _admin_client()
        .table("calendar_events")
        .insert(payload)
        .execute()
    )

    return result.data[0] if result.data else None


def get_calendar_event(
    user_id: str,
    event_id: str,
) -> dict | None:
    """Obtiene un evento verificando que pertenezca al usuario."""
    result = (
        _admin_client()
        .table("calendar_events")
        .select("*")
        .eq("id", event_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )

    return result.data[0] if result.data else None


def get_active_calendar_events(
    user_id: str,
    limit: int = 10,
    offset: int = 0,
    now: datetime | None = None,
) -> list[dict]:
    """Obtiene los próximos eventos activos del usuario.

    Los eventos se devuelven ordenados desde el más próximo.
    """
    if limit < 1:
        raise ValueError("El límite debe ser mayor que cero")

    if offset < 0:
        raise ValueError("El desplazamiento no puede ser negativo")

    current = now or _utc_now()

    result = (
        _admin_client()
        .table("calendar_events")
        .select("*")
        .eq("user_id", user_id)
        .eq("status", "active")
        .gte("event_at", _iso_utc(current))
        .order("event_at", desc=False)
        .range(offset, offset + limit - 1)
        .execute()
    )

    return result.data or []


def update_calendar_event_date(
    user_id: str,
    event_id: str,
    event_at: datetime,
    reminder_at: datetime,
) -> dict | None:
    """Cambia la fecha de un evento activo.

    Se mantiene el mismo ID para no crear registros duplicados. Los intentos
    pendientes asociados a la fecha anterior se cancelan, pero el historial
    de mensajes ya enviados se conserva.
    """
    if reminder_at > event_at:
        raise ValueError(
            "La fecha del recordatorio no puede ser posterior al evento"
        )

    current_event = get_calendar_event(user_id, event_id)
    if not current_event:
        return None

    if current_event.get("status") != "active":
        return None

    changes = {
        "event_at": _iso_utc(event_at),
        "reminder_at": _iso_utc(reminder_at),
    }

    result = (
        _admin_client()
        .table("calendar_events")
        .update(changes)
        .eq("id", event_id)
        .eq("user_id", user_id)
        .eq("status", "active")
        .execute()
    )

    if not result.data:
        return None

    cancel_pending_calendar_deliveries(event_id)
    return result.data[0]


def update_calendar_event_description(
    user_id: str,
    event_id: str,
    description: str,
) -> dict | None:
    """Actualiza la descripción de un evento activo."""
    normalized_description = _normalize_description(description)

    result = (
        _admin_client()
        .table("calendar_events")
        .update({
            "description": normalized_description,
        })
        .eq("id", event_id)
        .eq("user_id", user_id)
        .eq("status", "active")
        .execute()
    )

    return result.data[0] if result.data else None


def cancel_calendar_event(
    user_id: str,
    event_id: str,
) -> dict | None:
    """Realiza la eliminación lógica de un evento.

    El evento desaparece de las consultas activas, pero permanece disponible
    para auditoría.
    """
    now_iso = _iso_utc(_utc_now())

    result = (
        _admin_client()
        .table("calendar_events")
        .update({
            "status": "cancelled",
            "cancelled_at": now_iso,
        })
        .eq("id", event_id)
        .eq("user_id", user_id)
        .eq("status", "active")
        .execute()
    )

    if not result.data:
        return None

    cancel_pending_calendar_deliveries(event_id)
    return result.data[0]


def complete_calendar_event(
    user_id: str,
    event_id: str,
) -> dict | None:
    """Marca un evento como completado.

    No es obligatorio para los tres criterios iniciales, pero la tabla ya
    permite incorporarlo posteriormente sin modificar la estructura.
    """
    now_iso = _iso_utc(_utc_now())

    result = (
        _admin_client()
        .table("calendar_events")
        .update({
            "status": "completed",
            "completed_at": now_iso,
        })
        .eq("id", event_id)
        .eq("user_id", user_id)
        .eq("status", "active")
        .execute()
    )

    if not result.data:
        return None

    cancel_pending_calendar_deliveries(event_id)
    return result.data[0]


# ============================================================
# SESIONES CONVERSACIONALES
# ============================================================

def get_calendar_session(user_id: str) -> dict | None:
    """Obtiene la sesión activa del calendario para un usuario."""
    result = (
        _admin_client()
        .table("calendar_sessions")
        .select("*")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )

    return result.data[0] if result.data else None


def start_calendar_session(
    user_id: str,
    state: str,
    event_id: str | None = None,
) -> dict | None:
    """Crea o reinicia la sesión del calendario.

    Solamente puede existir una sesión de calendario por usuario debido a
    que user_id es la clave primaria de calendar_sessions.
    """
    if state not in VALID_SESSION_STATES:
        raise ValueError(
            f"Estado de sesión de calendario inválido: {state}"
        )

    payload = {
        "user_id": user_id,
        "state": state,
        "event_id": event_id,
        "draft_event_at": None,
        "draft_description": None,
        "updated_at": _iso_utc(_utc_now()),
    }

    result = (
        _admin_client()
        .table("calendar_sessions")
        .upsert(payload, on_conflict="user_id")
        .execute()
    )

    return result.data[0] if result.data else None


def update_calendar_session(
    user_id: str,
    *,
    state: str | None = None,
    event_id: Any = _UNSET,
    draft_event_at: Any = _UNSET,
    draft_description: Any = _UNSET,
) -> dict | None:
    """Actualiza únicamente los campos entregados de la sesión.

    El valor None permite limpiar explícitamente un campo. Si el parámetro
    no se entrega, el campo conserva su valor anterior.
    """
    changes: dict[str, Any] = {
        "updated_at": _iso_utc(_utc_now()),
    }

    if state is not None:
        if state not in VALID_SESSION_STATES:
            raise ValueError(
                f"Estado de sesión de calendario inválido: {state}"
            )
        changes["state"] = state

    if event_id is not _UNSET:
        changes["event_id"] = event_id

    if draft_event_at is not _UNSET:
        changes["draft_event_at"] = (
            _iso_utc(draft_event_at)
            if isinstance(draft_event_at, datetime)
            else draft_event_at
        )

    if draft_description is not _UNSET:
        changes["draft_description"] = (
            _normalize_description(draft_description)
            if draft_description is not None
            else None
        )

    result = (
        _admin_client()
        .table("calendar_sessions")
        .update(changes)
        .eq("user_id", user_id)
        .execute()
    )

    return result.data[0] if result.data else None


def clear_calendar_session(user_id: str) -> None:
    """Elimina el borrador o flujo activo de un usuario."""
    (
        _admin_client()
        .table("calendar_sessions")
        .delete()
        .eq("user_id", user_id)
        .execute()
    )


def calendar_session_is_active(user_id: str) -> bool:
    """Indica si el usuario tiene un flujo de calendario pendiente."""
    return get_calendar_session(user_id) is not None


# ============================================================
# EVENTOS QUE DEBEN ENVIARSE
# ============================================================

def get_due_calendar_events(
    limit: int,
    now: datetime | None = None,
) -> list[dict]:
    """Obtiene eventos cuyo recordatorio ya está vencido.

    La relación con users permite obtener el teléfono y verificar el
    consentimiento global de recordatorios.

    Se excluyen los eventos que ya tengan una entrega pendiente o exitosa
    para el mismo scheduled_for. Los envíos fallidos pueden volver a
    intentarse.
    """
    if limit < 1:
        raise ValueError("El límite debe ser mayor que cero")

    client = _admin_client()
    current = now or _utc_now()

    # Para el PMV se inspecciona un grupo mayor que el batch solicitado.
    # Esto permite filtrar eventos que ya tienen una entrega registrada.
    scan_limit = max(limit * 10, 1000)

    result = (
        client
        .table("calendar_events")
        .select(
            "id,user_id,description,event_at,reminder_at,status,"
            "users!calendar_events_user_id_fkey!inner("
            "phone,reminders_enabled"
            ")"
        )
        .eq("status", "active")
        .eq("users.reminders_enabled", True)
        .lte("reminder_at", _iso_utc(current))
        .order("reminder_at", desc=False)
        .limit(scan_limit)
        .execute()
    )

    candidates = result.data or []
    if not candidates:
        return []

    event_ids = [
        event["id"]
        for event in candidates
        if event.get("id")
    ]

    deliveries_result = (
        client
        .table("calendar_deliveries")
        .select("event_id,scheduled_for,delivery_status")
        .in_("event_id", event_ids)
        .execute()
    )

    blocking_statuses = {
        "pending",
        "sent",
        "delivered",
        "read",
    }

    blocked_schedules = {
        (
            row.get("event_id"),
            _timestamp_key(row.get("scheduled_for")),
        )
        for row in (deliveries_result.data or [])
        if row.get("delivery_status") in blocking_statuses
    }

    due_events: list[dict] = []

    for event in candidates:
        schedule_key = (
            event.get("id"),
            _timestamp_key(event.get("reminder_at")),
        )

        if schedule_key in blocked_schedules:
            continue

        user_data = event.pop("users", None)

        # Una relación many-to-one normalmente llega como dict. Esta
        # normalización también tolera que PostgREST la entregue como lista.
        if isinstance(user_data, list):
            user_data = user_data[0] if user_data else None

        if not isinstance(user_data, dict):
            logger.warning(
                "El evento %s no contiene un usuario relacionado",
                event.get("id"),
            )
            continue

        phone = user_data.get("phone")
        if not phone:
            logger.warning(
                "El evento %s pertenece a un usuario sin teléfono",
                event.get("id"),
            )
            continue

        event["phone"] = phone
        event["reminders_enabled"] = bool(
            user_data.get("reminders_enabled")
        )

        due_events.append(event)

        if len(due_events) >= limit:
            break

    return due_events


# ============================================================
# ENTREGAS
# ============================================================

def create_calendar_delivery(
    event_id: str,
    scheduled_for: str | datetime,
) -> str | None:
    """Reserva el envío de un recordatorio.

    La restricción única (event_id, scheduled_for) impide que dos
    ejecuciones del cron envíen el mismo aviso.

    Si existe un intento fallido, puede reutilizarse.
    """
    client = _admin_client()
    scheduled_iso = _timestamp_key(scheduled_for)

    try:
        result = (
            client
            .table("calendar_deliveries")
            .insert({
                "event_id": event_id,
                "scheduled_for": scheduled_iso,
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
                .table("calendar_deliveries")
                .select("id,delivery_status,attempt_count")
                .eq("event_id", event_id)
                .eq("scheduled_for", scheduled_iso)
                .limit(1)
                .execute()
            )

            if not existing.data:
                logger.error(
                    "No se pudo reservar el recordatorio del evento %s: %s",
                    event_id,
                    error,
                )
                return None

            delivery = existing.data[0]

            if delivery.get("delivery_status") != "failed":
                logger.info(
                    "El recordatorio del evento %s para %s ya estaba "
                    "reservado o enviado",
                    event_id,
                    scheduled_iso,
                )
                return None

            delivery_id = delivery["id"]
            attempt_count = int(
                delivery.get("attempt_count") or 0
            ) + 1

            retry_result = (
                client
                .table("calendar_deliveries")
                .update({
                    "delivery_status": "pending",
                    "provider_message_id": None,
                    "failure_reason": None,
                    "attempt_count": attempt_count,
                    "sent_at": None,
                    "delivered_at": None,
                    "read_at": None,
                })
                .eq("id", delivery_id)
                .eq("delivery_status", "failed")
                .execute()
            )

            return delivery_id if retry_result.data else None

        except Exception as lookup_error:
            logger.error(
                "No se pudo consultar el intento del evento %s: %s",
                event_id,
                lookup_error,
            )
            return None


def mark_calendar_delivery_sent(
    delivery_id: str,
    provider_message_id: str,
) -> None:
    """Registra que Meta aceptó el envío."""
    now_iso = _iso_utc(_utc_now())

    (
        _admin_client()
        .table("calendar_deliveries")
        .update({
            "delivery_status": "sent",
            "provider_message_id": provider_message_id,
            "sent_at": now_iso,
            "failure_reason": None,
        })
        .eq("id", delivery_id)
        .execute()
    )


def mark_calendar_delivery_failed(
    delivery_id: str,
    reason: str,
) -> None:
    """Registra un intento fallido."""
    (
        _admin_client()
        .table("calendar_deliveries")
        .update({
            "delivery_status": "failed",
            "failure_reason": (reason or "Error desconocido")[:2000],
        })
        .eq("id", delivery_id)
        .execute()
    )


def cancel_pending_calendar_deliveries(event_id: str) -> None:
    """Cancela reservas pendientes cuando el evento cambia o se elimina."""
    client = _optional_admin_client()
    if client is None:
        return

    try:
        (
            client
            .table("calendar_deliveries")
            .update({
                "delivery_status": "cancelled",
            })
            .eq("event_id", event_id)
            .eq("delivery_status", "pending")
            .execute()
        )
    except Exception as error:
        logger.error(
            "No se pudieron cancelar las entregas pendientes "
            "del evento %s: %s",
            event_id,
            error,
        )


def update_calendar_delivery_status(
    provider_message_id: str,
    status: str,
    event_timestamp: str | int | None = None,
    failure_reason: str | None = None,
) -> bool:
    """Actualiza un envío usando los webhooks de estado de Meta.

    Retorna True si provider_message_id pertenecía a un recordatorio del
    calendario. Esto permite que webhook.py pruebe otros tipos de entregas
    cuando el resultado sea False.
    """
    if not provider_message_id:
        return False

    if status not in VALID_DELIVERY_STATUSES:
        return False

    client = _optional_admin_client()
    if client is None:
        return False

    existing = (
        client
        .table("calendar_deliveries")
        .select("id,delivery_status")
        .eq("provider_message_id", provider_message_id)
        .limit(1)
        .execute()
    )

    if not existing.data:
        return False

    delivery = existing.data[0]
    current_status = delivery.get("delivery_status", "pending")

    # Ignora actualizaciones atrasadas. Por ejemplo, no cambia "read"
    # nuevamente a "delivered".
    if (
        status != "failed"
        and _DELIVERY_STATUS_RANK.get(status, 0)
        < _DELIVERY_STATUS_RANK.get(current_status, 0)
    ):
        return True

    try:
        occurred_at = (
            datetime.fromtimestamp(
                int(event_timestamp),
                tz=timezone.utc,
            )
            if event_timestamp
            else _utc_now()
        )
    except (TypeError, ValueError, OSError):
        occurred_at = _utc_now()

    changes: dict[str, Any] = {
        "delivery_status": status,
    }

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
        .table("calendar_deliveries")
        .update(changes)
        .eq("id", delivery["id"])
        .execute()
    )

    return True
