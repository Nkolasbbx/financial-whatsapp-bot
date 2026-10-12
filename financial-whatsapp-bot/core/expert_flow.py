"""Contacto con un experto (HdU16) — lado del emprendedor.

El emprendedor pide hablar con un experto de su comuna. El bot le pregunta el
tema (lista) y un detalle opcional; mientras tanto la solicitud está en estado
'draft' y el admin no la ve. Al terminar pasa a 'pending' y aparece en el
panel de su comuna. Cuando el admin la toma, la conversación pasa por el
número del bot (relé): el asistente de IA queda en pausa para ese
emprendedor y sus mensajes van al panel hasta que escriba "terminar" o el
admin cierre la solicitud.
"""
import logging
from datetime import datetime, timedelta, timezone

from config import ADMIN_ACCOUNTS
from db.expert_requests import (
    add_expert_message,
    create_expert_request,
    delete_expert_draft,
    finalize_expert_request,
    get_open_expert_request,
    parse_timestamp,
    resolve_expert_request,
    set_expert_topic,
    touch_user_message,
)

logger = logging.getLogger("financial")

EXPERT_ENTRY_ID = "menu_experto"
EXPERT_RELAY = "__EXPERT_RELAY__"

TOPIC_PREFIX = "experto_tema:"
CANCEL_ID = "experto_cancelar"
SKIP_ID = "experto_omitir"

# Mismos temas que clasifican los insights del admin (db/assistant_feedback.py).
TOPICS = (
    ("formalizacion", "Formalización"),
    ("patentes", "Patentes"),
    ("sii", "SII e impuestos"),
    ("permisos", "Permisos"),
    ("fondos", "Fondos"),
    ("otro", "Otro"),
)
TOPIC_LABELS = dict(TOPICS)

MAX_REASON_CHARS = 300
DRAFT_TTL = timedelta(minutes=30)

# Plazo que se le promete al emprendedor. La ventana de WhatsApp para texto
# libre dura 24 h desde su último mensaje, así que no conviene prometer más.
RESPONSE_DEADLINE_TEXT = "dentro de 24 horas"

_ENTRY_PHRASES = (
    "hablar con un experto", "hablar con experto", "contactar a un experto",
    "contactar un experto", "contactar experto", "necesito un experto",
    "quiero un experto", "hablar con una persona",
)
_END_COMMANDS = {"terminar", "terminar conversacion", "terminar conversación", "finalizar"}
_MENU_WORDS = {"menu", "menú", "menu_financial"}


def _served_comunas() -> set[str]:
    return {account["comuna"].strip().lower() for account in ADMIN_ACCOUNTS.values()}


def is_expert_entry_message(message: str) -> bool:
    raw = message.strip().lower()
    return raw == EXPERT_ENTRY_ID or any(phrase in raw for phrase in _ENTRY_PHRASES)


def _is_end_command(message: str) -> bool:
    return message.strip().lower().rstrip(".!") in _END_COMMANDS


def _topic_widget(intro: str = "") -> dict:
    return {
        "type": "list",
        "body": f"{intro}¿Sobre qué necesitas ayuda del experto? Elige un tema 👇",
        "button_text": "Elegir tema",
        "options": [(TOPIC_PREFIX + key, label) for key, label in TOPICS]
        + [(CANCEL_ID, "❌ Cancelar")],
    }


def _detail_widget(topic_label: str) -> dict:
    return {
        "type": "buttons",
        "body": (
            f"Tema: *{topic_label}*.\n\nCuéntame en una o dos frases qué necesitas "
            "(así el experto llega preparado), o toca *Omitir*."
        ),
        "options": [(SKIP_ID, "Omitir"), (CANCEL_ID, "Cancelar")],
    }


def _draft_expired(draft: dict) -> bool:
    created = parse_timestamp(draft.get("created_at"))
    return bool(created and datetime.now(timezone.utc) - created > DRAFT_TTL)


def _handle_draft(draft: dict, message: str, reset_commands: set[str]):
    raw = message.strip().lower()

    if raw in (CANCEL_ID, "cancelar"):
        delete_expert_draft(draft["id"])
        return "Listo, cancelé la solicitud. Puedes pedir un experto otra vez desde el menú cuando quieras."
    if raw in reset_commands or raw in _MENU_WORDS:
        delete_expert_draft(draft["id"])
        return None

    if not draft.get("topic"):
        key = raw[len(TOPIC_PREFIX):] if raw.startswith(TOPIC_PREFIX) else ""
        if key in TOPIC_LABELS:
            set_expert_topic(draft["id"], key)
            return _detail_widget(TOPIC_LABELS[key])
        return _topic_widget("Para continuar, elige un tema de la lista (o cancela). ")

    reason = None if raw in (SKIP_ID, "omitir") else message.strip()[:MAX_REASON_CHARS]
    if not finalize_expert_request(draft["id"], reason):
        return "No pude registrar tu solicitud en este momento. Inténtalo más tarde."
    return (
        f"✅ Listo, pedí que un experto de tu municipalidad te contacte por "
        f"*{TOPIC_LABELS.get(draft['topic'], 'tu consulta')}*.\n\n"
        f"Te escribirá por este mismo chat {RESPONSE_DEADLINE_TEXT}. Mientras "
        "esperas, puedes seguir usando el asistente."
    )


def handle_expert_message(user: dict, message: str, reset_commands: set[str]):
    """Devuelve la respuesta para el emprendedor (texto o widget), EXPERT_RELAY si
    el mensaje se reenvió al panel (sin respuesta), o None si no aplica y sigue
    el flujo normal."""
    user_id = user.get("id")
    if not user_id:
        return None

    try:
        open_request = get_open_expert_request(user_id)

        if open_request and open_request["status"] == "draft":
            if _draft_expired(open_request):
                delete_expert_draft(open_request["id"])
                open_request = None
            else:
                return _handle_draft(open_request, message, reset_commands)

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

        if not create_expert_request(user_id, comuna, status="draft"):
            return "No pude registrar tu solicitud en este momento. Inténtalo más tarde."
        return _topic_widget()
    except Exception as error:
        logger.exception("Error en el flujo de contacto con experto: %s", error)
        return None
