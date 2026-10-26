"""Envío del aviso de las convocatorias comunitarias (HdU019).

Sigue el mismo patrón que services/calendar_reminders.py: reserva la entrega
antes de enviar (la restricción única evita duplicados entre ciclos del
cron), envía la plantilla aprobada por Meta y registra el resultado.
"""

from __future__ import annotations

import asyncio
import logging

from config import (
    COMMUNITY_BATCH_SIZE,
    COMMUNITY_NOTICES_ENABLED,
    COMMUNITY_TEMPLATE_LANGUAGE,
    COMMUNITY_TEMPLATE_NAME,
)
from core.community_events import build_notice_template_parameters
from db.community_events import (
    get_due_community_events,
    get_pending_recipients,
    mark_community_delivery_failed,
    mark_community_delivery_sent,
    mark_community_event_notified,
    reserve_community_delivery,
)
from services.whatsapp import extract_provider_message_id, send_template

logger = logging.getLogger("financial")


def _disabled_counters() -> dict:
    return {
        "community_status": "disabled",
        "community_events": 0,
        "community_sent": 0,
        "community_failed": 0,
        "community_skipped": 0,
        "community_completed": 0,
    }


async def _send_to_recipient(event: dict, recipient: dict, parameters: list[str]) -> str:
    """Envía el aviso a un destinatario. Retorna sent, failed o skipped."""
    delivery_id = await asyncio.to_thread(
        reserve_community_delivery,
        event["id"],
        recipient["user_id"],
    )
    if delivery_id is None:
        return "skipped"

    try:
        response = await send_template(
            recipient["phone"],
            COMMUNITY_TEMPLATE_NAME,
            COMMUNITY_TEMPLATE_LANGUAGE,
            parameters,
        )
        provider_message_id = extract_provider_message_id(response)
        if not provider_message_id:
            raise RuntimeError("Meta no devolvió el identificador del mensaje")

        await asyncio.to_thread(
            mark_community_delivery_sent,
            delivery_id,
            provider_message_id,
        )
        return "sent"
    except Exception as error:
        logger.exception(
            "No se pudo enviar el aviso de la convocatoria %s",
            event.get("id"),
        )
        try:
            await asyncio.to_thread(
                mark_community_delivery_failed,
                delivery_id,
                str(error),
            )
        except Exception as persistence_error:
            logger.error(
                "No se pudo registrar el fallo del aviso %s: %s",
                delivery_id,
                persistence_error,
            )
        return "failed"


async def send_due_community_notices() -> dict:
    """Envía los avisos vencidos a los usuarios de cada comuna destinataria.

    COMMUNITY_BATCH_SIZE limita los mensajes por ciclo; si una comuna tiene
    más destinatarios, el ciclo siguiente continúa donde quedó.
    """
    if not COMMUNITY_NOTICES_ENABLED:
        return _disabled_counters()

    if not COMMUNITY_TEMPLATE_NAME or not COMMUNITY_TEMPLATE_LANGUAGE:
        raise RuntimeError("La plantilla de convocatorias no está configurada")

    events = await asyncio.to_thread(get_due_community_events, COMMUNITY_BATCH_SIZE)
    counters = {
        "community_status": "completed",
        "community_events": len(events),
        "community_sent": 0,
        "community_failed": 0,
        "community_skipped": 0,
        "community_completed": 0,
    }
    budget = COMMUNITY_BATCH_SIZE

    for event in events:
        if budget < 1:
            break

        recipients = await asyncio.to_thread(get_pending_recipients, event, budget)
        if recipients:
            parameters = build_notice_template_parameters(
                event,
                event.get("created_by") or "",
            )
            for recipient in recipients:
                budget -= 1
                result = await _send_to_recipient(event, recipient, parameters)
                counters[f"community_{result}"] += 1

            # Los fallidos con intentos restantes siguen pendientes y se
            # reintentan en el próximo ciclo; solo se cierra el aviso cuando
            # ya no queda nadie por atender.
            remaining = await asyncio.to_thread(get_pending_recipients, event, 1)
            if remaining:
                continue

        await asyncio.to_thread(mark_community_event_notified, event["id"])
        counters["community_completed"] += 1

    return counters
