"""Contacto con un experto (HdU16) — lado del emprendedor.

El emprendedor pide hablar con un experto de su comuna → se crea una
solicitud que ve el admin de esa comuna en su panel. Cuando el admin la toma,
la conversación pasa por el número del bot (relé): el asistente de IA queda en
pausa para ese emprendedor y sus mensajes van al panel hasta que escriba
"terminar" o el admin cierre la solicitud.
"""
import logging

from config import ADMIN_ACCOUNTS
from db.expert_requests import (
    add_expert_message,
    create_expert_request,
    get_open_expert_request,
    resolve_expert_request,
    touch_user_message,
)

logger = logging.getLogger("financial")

EXPERT_ENTRY_ID = "menu_experto"
EXPERT_RELAY = "__EXPERT_RELAY__"

# Plazo que se le promete al emprendedor. La ventana de WhatsApp para texto
# libre dura 24 h desde su último mensaje, así que no conviene prometer más.
RESPONSE_DEADLINE_TEXT = "dentro de 24 horas"

_ENTRY_PHRASES = (
    "hablar con un experto", "hablar con experto", "contactar a un experto",
    "contactar un experto", "contactar experto", "necesito un experto",
    "quiero un experto", "hablar con una persona",
)
_END_COMMANDS = {"terminar", "terminar conversacion", "terminar conversación", "finalizar"}


def _served_comunas() -> set[str]:
    return {account["comuna"].strip().lower() for account in ADMIN_ACCOUNTS.values()}


def is_expert_entry_message(message: str) -> bool:
    raw = message.strip().lower()
    return raw == EXPERT_ENTRY_ID or any(phrase in raw for phrase in _ENTRY_PHRASES)


def _is_end_command(message: str) -> bool:
    return message.strip().lower().rstrip(".!") in _END_COMMANDS


def handle_expert_message(user: dict, message: str, reset_commands: set[str]) -> str | None:
    """Devuelve la respuesta para el emprendedor, EXPERT_RELAY si el mensaje se
    reenvió al panel (sin respuesta), o None si no aplica y sigue el flujo normal."""
    user_id = user.get("id")
    if not user_id:
        return None

    try:
        open_request = get_open_expert_request(user_id)

        if open_request and open_request["status"] == "in_progress":
            if message.strip().lower() in reset_commands:
                resolve_expert_request(open_request["id"], "emprendedor (reinicio)")
                return None
            if _is_end_command(message):
                resolve_expert_request(open_request["id"], "emprendedor")
                return (
                    "✅ Terminó la conversación con el experto. Vuelves a hablar "
                    "con el asistente. Si necesitas al experto otra vez, puedes "
                    "pedirlo desde el menú."
                )
            add_expert_message(open_request["id"], "user", message)
            touch_user_message(open_request["id"])
            return EXPERT_RELAY

        if open_request:  # pendiente: el asistente sigue funcionando normal
            touch_user_message(open_request["id"])
            if is_expert_entry_message(message):
                return (
                    "Ya tienes una solicitud en curso 🙌 Un experto te contactará "
                    f"por este chat {RESPONSE_DEADLINE_TEXT}."
                )
            return None

        if not is_expert_entry_message(message):
            return None

        comuna = (user.get("comuna") or "").strip()
        if comuna.lower() not in _served_comunas():
            return (
                "Por ahora el contacto con expertos está disponible solo para "
                "Recoleta y El Bosque. Mientras tanto, puedo ayudarte yo con tus "
                "dudas. 😊"
            )

        if not create_expert_request(user_id, comuna):
            return "No pude registrar tu solicitud en este momento. Inténtalo más tarde."
        return (
            "✅ Listo, pedí que un experto de tu municipalidad te contacte.\n\n"
            f"Te escribirá por este mismo chat {RESPONSE_DEADLINE_TEXT}. Mientras "
            "esperas, puedes seguir usando el asistente."
        )
    except Exception as error:
        logger.exception("Error en el flujo de contacto con experto: %s", error)
        return None
