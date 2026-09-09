from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config import (
    CALENDAR_BATCH_SIZE,
    CALENDAR_REMINDERS_ENABLED,
    CALENDAR_TEMPLATE_LANGUAGE,
    CALENDAR_TEMPLATE_NAME,
    REMINDER_TIMEZONE,
)
from db.calendar import (
    create_calendar_delivery,
    get_due_calendar_events,
    mark_calendar_delivery_failed,
    mark_calendar_delivery_sent,
)
from services.whatsapp import extract_provider_message_id, send_template

logger = logging.getLogger("financial")

_SPANISH_MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_calendar_template_date(value: str | datetime) -> str:
    """Fecha legible para la variable {{2}} de la plantilla de Meta."""
    try:
        local_tz = ZoneInfo(REMINDER_TIMEZONE)
    except Exception:
        local_tz = ZoneInfo("UTC")
    local_value = _parse_datetime(value).astimezone(local_tz)
    return (
        f"{local_value.day} de {_SPANISH_MONTHS[local_value.month - 1]} de "
        f"{local_value.year} a las {local_value:%H:%M}"
    )


def build_calendar_template_parameters(event: dict) -> list[str]:
    """Orden esperado en Meta: {{1}} descripción y {{2}} fecha del evento."""
    description = " ".join(
        (event.get("description") or "Compromiso de tu negocio").split()
    )
    return [
        description,
        format_calendar_template_date(event["event_at"]),
    ]


async def send_due_calendar_reminders() -> dict:
    """Envía una vez cada fecha personal vencida y registra su resultado."""
    if not CALENDAR_REMINDERS_ENABLED:
        return {
            "calendar_status": "disabled",
            "calendar_processed": 0,
            "calendar_sent": 0,
            "calendar_failed": 0,
            "calendar_skipped": 0,
        }

    if not CALENDAR_TEMPLATE_NAME or not CALENDAR_TEMPLATE_LANGUAGE:
        raise RuntimeError("La plantilla del calendario no está configurada")

    events = await asyncio.to_thread(get_due_calendar_events, CALENDAR_BATCH_SIZE)
    counters = {
        "calendar_status": "completed",
        "calendar_processed": len(events),
        "calendar_sent": 0,
        "calendar_failed": 0,
        "calendar_skipped": 0,
    }

    for event in events:
        delivery_id = await asyncio.to_thread(
            create_calendar_delivery,
            event["id"],
            event["reminder_at"],
        )
        if delivery_id is None:
            counters["calendar_skipped"] += 1
            continue

        try:
            response = await send_template(
                event["phone"],
                CALENDAR_TEMPLATE_NAME,
                CALENDAR_TEMPLATE_LANGUAGE,
                build_calendar_template_parameters(event),
            )
            provider_message_id = extract_provider_message_id(response)
            if not provider_message_id:
                raise RuntimeError("Meta no devolvió el identificador del mensaje")

            await asyncio.to_thread(
                mark_calendar_delivery_sent,
                delivery_id,
                provider_message_id,
            )
            counters["calendar_sent"] += 1
        except Exception as error:
            logger.exception(
                "No se pudo enviar el recordatorio del evento %s",
                event.get("id"),
            )
            try:
                await asyncio.to_thread(
                    mark_calendar_delivery_failed,
                    delivery_id,
                    str(error),
                )
            except Exception as persistence_error:
                logger.error(
                    "No se pudo registrar el fallo del envío %s: %s",
                    delivery_id,
                    persistence_error,
                )
            counters["calendar_failed"] += 1

    return counters
