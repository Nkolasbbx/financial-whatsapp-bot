import logging

from db.users import get_user, save_user
from core.roadmaps import get_roadmap_text, mark_hito_done
from core.fondos import simulate_funds
from core.onboarding import process_onboarding
from db.reminders import (
    disable_reminders,
    enable_reminders,
    record_incoming_reminder_reply,
    record_roadmap_activity,
)

logger = logging.getLogger("financial")


def _record_reply_safely(phone: str, reply_to_message_id: str | None) -> bool:
    try:
        return record_incoming_reminder_reply(phone, reply_to_message_id)
    except Exception as error:
        logger.error("No se pudo registrar la respuesta al recordatorio: %s", error)
        return False


def _record_activity_safely(phone: str) -> None:
    try:
        record_roadmap_activity(phone)
    except Exception as error:
        logger.error("No se pudo registrar la actividad del roadmap: %s", error)


def route_message(
    phone: str,
    message: str,
    reply_to_message_id: str | None = None,
) -> str:
    """Main router: determines what to do with each incoming message."""
    message = message.strip()
    msg_lower = message.lower()

    # Get or create user
    user = get_user(phone)

    if not user:
        user = {"phone": phone, "onboarding_step": 0}
        save_user(phone, user)

    # ── Onboarding flow ──
    if user.get("onboarding_step") != "done":
        response = process_onboarding(user, message, save_user)
        if response:
            return response

    # ── Reset command ──
    if msg_lower in ["reiniciar", "reset", "empezar de nuevo"]:
        from db.users import users_db
        users_db.pop(phone, None)
        new_user = {"phone": phone, "onboarding_step": 0}
        save_user(phone, new_user)
        return process_onboarding(new_user, message, save_user)

    activation_commands = {
        "activar recordatorios",
        "acepto recordatorios",
        "reanudar recordatorios",
    }
    if msg_lower in activation_commands:
        try:
            reminders_were_enabled = enable_reminders(phone)
        except Exception as error:
            logger.error("No se pudieron activar los recordatorios: %s", error)
            reminders_were_enabled = False
        if not reminders_were_enabled:
            return (
                "No pude activar los recordatorios en este momento. "
                "Inténtalo nuevamente más tarde."
            )
        return (
            "🔔 *Recordatorios activados.*\n\n"
            "Si pasan 3 días sin que avances en tu roadmap, te enviaré un "
            "recordatorio por WhatsApp. Puedes pausarlos cuando quieras "
            "escribiendo *\"pausar recordatorios\"*."
        )

    pause_commands = {
        "pausar recordatorios",
        "desactivar recordatorios",
        "no quiero recordatorios",
    }
    if msg_lower.rstrip(".") in pause_commands:
        _record_reply_safely(phone, reply_to_message_id)
        try:
            reminders_were_disabled = disable_reminders(phone)
        except Exception as error:
            logger.error("No se pudieron pausar los recordatorios: %s", error)
            reminders_were_disabled = False
        if not reminders_were_disabled:
            return (
                "No pude pausar los recordatorios en este momento. "
                "Inténtalo nuevamente más tarde."
            )
        return (
            "🔕 *Recordatorios pausados.*\n\n"
            "Puedes volver a activarlos cuando quieras escribiendo "
            "*\"activar recordatorios\"*."
        )

    replied_to_reminder = False
    if reply_to_message_id or int(user.get("reminder_count") or 0) > 0:
        replied_to_reminder = _record_reply_safely(
            phone,
            reply_to_message_id,
        )

    # ── Roadmap commands ──
    roadmap_triggers = ["roadmap", "mi roadmap", "hitos", "qué me falta", "que me falta", "formalizar", "mis pasos", "mi ruta"]
    if any(trigger in msg_lower for trigger in roadmap_triggers):
        _record_activity_safely(phone)
        return get_roadmap_text(user)

    # ── Mark hito done ──
    done_triggers = ["listo", "hecho", "completado", "ya lo hice", "ya está", "ya esta", "siguiente"]
    if any(trigger in msg_lower for trigger in done_triggers):
        response = mark_hito_done(user, save_user)
        _record_activity_safely(phone)
        return response

    if replied_to_reminder:
        _record_activity_safely(phone)
        return get_roadmap_text(user)

    # ── Fund simulation ──
    fund_triggers = ["fondo", "postular", "capital semilla", "capital abeja", "sercotec", "corfo", "financiamiento"]
    if any(trigger in msg_lower for trigger in fund_triggers):
        return simulate_funds(user)

    # ── Help ──
    if msg_lower in ["ayuda", "help", "menu", "menú", "opciones"]:
        return (
            "📱 *Menú de FinancIAl*\n\n"
            "Escribe cualquiera de estas opciones:\n\n"
            "📋 *\"mi roadmap\"* → ver tu progreso de formalización\n"
            "✅ *\"listo\"* → marcar el hito actual como completado\n"
            "🎯 *\"postular a fondo\"* → simular postulación a fondos\n"
            "❓ *\"ayuda\"* → ver este menú\n"
            "🔄 *\"reiniciar\"* → empezar de nuevo\n\n"
            "🔔 *\"activar recordatorios\"* → recibir avisos de inactividad\n"
            "🔕 *\"pausar recordatorios\"* → detener los avisos\n\n"
            "💬 O simplemente *escribe tu pregunta* y te respondo con IA 🤖"
        )

    # ── AI Chat (default) ──
    return "__AI_QUERY__"


def split_message(text: str, max_len: int) -> list[str]:
    """Split long message into chunks at paragraph boundaries."""
    if len(text) <= max_len:
        return [text]

    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break

        # Find last newline before limit
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len

        parts.append(text[:split_at])
        text = text[split_at:].lstrip("\n")

    return parts
