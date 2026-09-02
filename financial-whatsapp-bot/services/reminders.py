import asyncio
import logging

from config import (
    REMINDER_BATCH_SIZE,
    REMINDER_FINAL_TEMPLATE_NAME,
    REMINDER_RECIPIENT_LABEL,
    REMINDER_TEMPLATE_LANGUAGE,
    REMINDER_TEMPLATE_NAME,
    REMINDERS_ENABLED,
)
from core.roadmaps import get_pending_milestone
from db.reminders import (
    clear_completed_roadmap_schedule,
    create_reminder_delivery,
    get_due_reminder_users,
    mark_reminder_failed,
    mark_reminder_sent,
)
from services.whatsapp import extract_provider_message_id, send_template_with_menu_followup

logger = logging.getLogger("financial")


def build_template_parameters(milestone_title: str) -> list[str]:
    """Orden aprobado en Meta: {{1}} destinatario y {{2}} hito pendiente."""
    return [REMINDER_RECIPIENT_LABEL or "emprendedor/a", milestone_title]


def select_reminder_template(reminder_number: int) -> str:
    """Los avisos 1–2 usan la plantilla normal y el tercero la plantilla final."""
    if reminder_number == 3:
        return REMINDER_FINAL_TEMPLATE_NAME
    return REMINDER_TEMPLATE_NAME


async def send_due_reminders() -> dict:
    """Envía como máximo un recordatorio pendiente por usuario y ejecución."""
    if not REMINDERS_ENABLED:
        return {
            "status": "disabled",
            "processed": 0,
            "sent": 0,
            "failed": 0,
            "skipped": 0,
        }

    if not all((
        REMINDER_TEMPLATE_NAME,
        REMINDER_FINAL_TEMPLATE_NAME,
        REMINDER_TEMPLATE_LANGUAGE,
    )):
        raise RuntimeError("Las plantillas de recordatorios no están configuradas")

    users = await asyncio.to_thread(
        get_due_reminder_users,
        REMINDER_BATCH_SIZE,
    )
    counters = {
        "status": "completed",
        "processed": len(users),
        "sent": 0,
        "failed": 0,
        "skipped": 0,
    }

    for user in users:
        milestone = get_pending_milestone(user)
        if milestone is None:
            await asyncio.to_thread(clear_completed_roadmap_schedule, user["id"])
            counters["skipped"] += 1
            continue

        reminder_number = int(user.get("reminder_count") or 0) + 1
        if reminder_number not in (1, 2, 3):
            counters["skipped"] += 1
            continue

        template_name = select_reminder_template(reminder_number)
        delivery_id = await asyncio.to_thread(
            create_reminder_delivery,
            user["id"],
            reminder_number,
            milestone["title"],
            template_name,
            user["next_reminder_at"],
        )
        if delivery_id is None:
            counters["skipped"] += 1
            continue

        try:
            response = await send_template_with_menu_followup(
                user["phone"],
                template_name,
                REMINDER_TEMPLATE_LANGUAGE,
                build_template_parameters(milestone["title"]),
            )
            provider_message_id = extract_provider_message_id(response)
            if not provider_message_id:
                raise RuntimeError("Meta no devolvió el identificador del mensaje")

            await asyncio.to_thread(
                mark_reminder_sent,
                delivery_id,
                user["id"],
                reminder_number,
                provider_message_id,
            )
            counters["sent"] += 1
        except Exception as error:
            logger.exception(
                "No se pudo enviar el recordatorio %s al usuario %s",
                reminder_number,
                user["id"],
            )
            try:
                await asyncio.to_thread(
                    mark_reminder_failed,
                    delivery_id,
                    str(error),
                )
            except Exception as persistence_error:
                logger.error(
                    "No se pudo persistir el fallo del recordatorio %s: %s",
                    delivery_id,
                    persistence_error,
                )
            counters["failed"] += 1

    return counters
