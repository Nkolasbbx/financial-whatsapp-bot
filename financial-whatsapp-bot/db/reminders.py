import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import REMINDER_DAYS, REMINDER_TIMEZONE

logger = logging.getLogger("financial")

_DELIVERY_STATUS_RANK = {
    "pending": 0,
    "sent": 1,
    "delivered": 2,
    "read": 3,
    "failed": 4,
}


def _admin_client():
    import dependencies

    if dependencies.supabase_admin is None:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY no está configurada; "
            "no se pueden procesar recordatorios"
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


def calculate_next_reminder_at(now: datetime | None = None) -> str:
    """Calcula el siguiente aviso respetando la zona horaria configurada."""
    current = now or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    local_current = current.astimezone(ZoneInfo(REMINDER_TIMEZONE))
    return _iso_utc(local_current + timedelta(days=REMINDER_DAYS))


def enable_reminders(phone: str) -> bool:
    client = _optional_admin_client()
    if client is None:
        logger.error("No se activaron recordatorios: falta Supabase administrativo")
        return False

    now = _utc_now()
    client.table("users").update({
        "reminders_enabled": True,
        "reminders_accepted_at": _iso_utc(now),
        "reminders_paused": False,
        "reminders_pause_reason": None,
        "reminder_count": 0,
        "last_roadmap_activity_at": _iso_utc(now),
        "last_reminder_at": None,
        "next_reminder_at": calculate_next_reminder_at(now),
        "updated_at": _iso_utc(now),
    }).eq("phone", phone).execute()
    return True


def disable_reminders(phone: str) -> bool:
    client = _optional_admin_client()
    if client is None:
        logger.error("No se pausaron recordatorios: falta Supabase administrativo")
        return False

    now = _utc_now()
    client.table("users").update({
        "reminders_enabled": False,
        "reminders_paused": True,
        "reminders_pause_reason": "user_opt_out",
        "next_reminder_at": None,
        "updated_at": _iso_utc(now),
    }).eq("phone", phone).execute()
    return True


def record_roadmap_activity(phone: str) -> None:
    client = _optional_admin_client()
    if client is None:
        return

    result = (
        client.table("users")
        .select("reminders_enabled")
        .eq("phone", phone)
        .limit(1)
        .execute()
    )
    if not result.data:
        return

    now = _utc_now()
    enabled = bool(result.data[0].get("reminders_enabled"))
    changes = {
        "last_roadmap_activity_at": _iso_utc(now),
        "reminder_count": 0,
        "next_reminder_at": calculate_next_reminder_at(now) if enabled else None,
        "updated_at": _iso_utc(now),
    }
    if enabled:
        changes.update({
            "reminders_paused": False,
            "reminders_pause_reason": None,
        })

    client.table("users").update(changes).eq("phone", phone).execute()


def get_due_reminder_users(limit: int) -> list[dict]:
    result = (
        _admin_client().table("users")
        .select(
            "id,phone,roadmap,reminder_count,next_reminder_at,"
            "reminders_enabled,reminders_paused"
        )
        .eq("reminders_enabled", True)
        .eq("reminders_paused", False)
        .lt("reminder_count", 3)
        .lte("next_reminder_at", _iso_utc(_utc_now()))
        .order("next_reminder_at")
        .limit(limit)
        .execute()
    )
    return result.data or []


def create_reminder_delivery(
    user_id: str,
    reminder_number: int,
    milestone_title: str,
    template_name: str,
    scheduled_for: str,
) -> str | None:
    """Reserva el envío; la restricción única evita ejecuciones duplicadas."""
    client = _admin_client()
    try:
        result = client.table("reminder_deliveries").insert({
            "user_id": user_id,
            "reminder_number": reminder_number,
            "milestone_title": milestone_title,
            "template_name": template_name,
            "delivery_status": "pending",
            "scheduled_for": scheduled_for,
        }).execute()
        return result.data[0]["id"] if result.data else None
    except Exception as error:
        # Un intento fallido se puede reutilizar. Los intentos pending/sent se
        # dejan intactos para impedir un envío doble por dos ejecuciones del cron.
        try:
            existing = (
                client.table("reminder_deliveries")
                .select("id,delivery_status")
                .eq("user_id", user_id)
                .eq("scheduled_for", scheduled_for)
                .eq("reminder_number", reminder_number)
                .limit(1)
                .execute()
            )
            if existing.data and existing.data[0].get("delivery_status") == "failed":
                delivery_id = existing.data[0]["id"]
                client.table("reminder_deliveries").update({
                    "delivery_status": "pending",
                    "failure_reason": None,
                    "provider_message_id": None,
                    "sent_at": None,
                    "delivered_at": None,
                    "read_at": None,
                }).eq("id", delivery_id).execute()
                return delivery_id
        except Exception as lookup_error:
            logger.error(
                "No se pudo consultar el intento fallido del usuario %s: %s",
                user_id,
                lookup_error,
            )

        logger.info(
            "El recordatorio %s del usuario %s ya estaba reservado o no pudo "
            "crearse: %s",
            reminder_number,
            user_id,
            error,
        )
        return None


def mark_reminder_sent(
    delivery_id: str,
    user_id: str,
    reminder_number: int,
    provider_message_id: str,
) -> None:
    client = _admin_client()
    now = _utc_now()
    now_iso = _iso_utc(now)
    client.table("reminder_deliveries").update({
        "delivery_status": "sent",
        "provider_message_id": provider_message_id,
        "sent_at": now_iso,
    }).eq("id", delivery_id).execute()

    user_changes = {
        "reminder_count": reminder_number,
        "last_reminder_at": now_iso,
        "updated_at": now_iso,
    }
    if reminder_number >= 3:
        user_changes.update({
            "reminders_paused": True,
            "reminders_pause_reason": "limit_reached",
            "next_reminder_at": None,
        })
    else:
        user_changes["next_reminder_at"] = calculate_next_reminder_at(now)

    client.table("users").update(user_changes).eq("id", user_id).execute()


def mark_reminder_failed(delivery_id: str, reason: str) -> None:
    _admin_client().table("reminder_deliveries").update({
        "delivery_status": "failed",
        "failure_reason": reason[:2000],
    }).eq("id", delivery_id).execute()


def update_reminder_delivery_status(
    provider_message_id: str,
    status: str,
    event_timestamp: str | int | None = None,
    failure_reason: str | None = None,
) -> None:
    if status not in _DELIVERY_STATUS_RANK or not provider_message_id:
        return

    client = _admin_client()
    existing = (
        client.table("reminder_deliveries")
        .select("id,delivery_status")
        .eq("provider_message_id", provider_message_id)
        .limit(1)
        .execute()
    )
    if not existing.data:
        return

    current_status = existing.data[0].get("delivery_status", "pending")
    if (
        status != "failed"
        and _DELIVERY_STATUS_RANK.get(status, 0)
        < _DELIVERY_STATUS_RANK.get(current_status, 0)
    ):
        return

    try:
        occurred_at = (
            datetime.fromtimestamp(int(event_timestamp), tz=timezone.utc)
            if event_timestamp
            else _utc_now()
        )
    except (TypeError, ValueError, OSError):
        occurred_at = _utc_now()

    changes = {"delivery_status": status}
    timestamp_column = {
        "sent": "sent_at",
        "delivered": "delivered_at",
        "read": "read_at",
    }.get(status)
    if timestamp_column:
        changes[timestamp_column] = _iso_utc(occurred_at)
    if status == "failed" and failure_reason:
        changes["failure_reason"] = failure_reason[:2000]

    client.table("reminder_deliveries").update(changes).eq(
        "id", existing.data[0]["id"]
    ).execute()


def record_incoming_reminder_reply(
    phone: str,
    provider_message_id: str | None = None,
) -> bool:
    """Marca el recordatorio citado o el último no respondido del usuario."""
    client = _optional_admin_client()
    if client is None:
        return False

    delivery = None
    if provider_message_id:
        result = (
            client.table("reminder_deliveries")
            .select("id")
            .eq("provider_message_id", provider_message_id)
            .is_("replied_at", "null")
            .limit(1)
            .execute()
        )
        if result.data:
            delivery = result.data[0]

    if delivery is None:
        user = (
            client.table("users")
            .select("id")
            .eq("phone", phone)
            .limit(1)
            .execute()
        )
        if not user.data:
            return False

        result = (
            client.table("reminder_deliveries")
            .select("id")
            .eq("user_id", user.data[0]["id"])
            .neq("delivery_status", "failed")
            .is_("replied_at", "null")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            delivery = result.data[0]

    if delivery is None:
        return False

    client.table("reminder_deliveries").update({
        "replied_at": _iso_utc(_utc_now()),
    }).eq("id", delivery["id"]).execute()
    return True


def clear_completed_roadmap_schedule(user_id: str) -> None:
    now = _iso_utc(_utc_now())
    _admin_client().table("users").update({
        "reminders_paused": True,
        "reminders_pause_reason": "manual",
        "reminder_count": 0,
        "next_reminder_at": None,
        "updated_at": now,
    }).eq("id", user_id).execute()
