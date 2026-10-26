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
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from config import ADMIN_ACCOUNTS
from db.expert_requests import (
    add_expert_message,
    create_expert_request,
    delete_expert_draft,
    finalize_expert_request,
    get_last_expert_message,
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
# Si el emprendedor lleva más de esto sin escribir, se le vuelve a acusar recibo.
ACK_AFTER = timedelta(hours=2)
DRAFT_TTL = timedelta(minutes=30)

# Plazo que se le promete al emprendedor. La ventana de WhatsApp para texto
# libre dura 24 h desde su último mensaje, así que no conviene prometer más.
RESPONSE_DEADLINE_TEXT = "dentro de 24 horas"

_ENTRY_PHRASES = (
    "hablar con un experto", "hablar con experto", "contactar a un experto",
    "contactar un experto", "contactar experto", "necesito un experto",
    "quiero un experto", "hablar con una persona",
    "hablar con un asesor", "hablar con asesor", "contactar a un asesor",
    "contactar un asesor", "contactar asesor", "necesito un asesor",
    "quiero un asesor",
)
_END_COMMANDS = {"terminar", "terminar conversacion", "terminar conversación", "finalizar"}
_MENU_WORDS = {"menu", "menú", "menu_financial"}


_TRAILING_PUNCTUATION = " .,!?¡¿;:\"'"

# Cómo se nombra cada tema cuando se dice en voz alta ("patentes", "sobre fondos").
_TOPIC_SYNONYMS = {
    "formalizacion": ("formalizacion", "formalizar"),
    "patentes": ("patente", "patentes"),
    "sii": ("sii", "impuesto", "impuestos"),
    "permisos": ("permiso", "permisos"),
    "fondos": ("fondo", "fondos"),
    "otro": ("otro", "otra"),
}


def _clean(message: str) -> str:
    """Minúsculas y sin puntuación en los bordes: Whisper transcribe "Omitir."."""
    return message.strip().lower().strip(_TRAILING_PUNCTUATION)


def _without_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def topic_from_text(message: str) -> str | None:
    """Tema nombrado en un mensaje hablado o escrito ("patentes", "es sobre fondos").
    Devuelve None si no nombra ninguno o nombra más de uno."""
    palabras = set(re.findall(r"[a-z0-9]+", _without_accents(message.lower())))
    encontrados = {key for key, sinonimos in _TOPIC_SYNONYMS.items() if palabras & set(sinonimos)}
    return encontrados.pop() if len(encontrados) == 1 else None


def _served_comunas() -> set[str]:
    return {account["comuna"].strip().lower() for account in ADMIN_ACCOUNTS.values()}


_NEGATIONS = ("no necesito", "no quiero", "no requiero", "ya no necesito", "ya no quiero")


_EXPERT_WORDS = ("experto", "asesor", "persona")


def is_cancel_intent(message: str) -> bool:
    """El emprendedor dice que ya no quiere al experto ("ya no necesito un asesor").
    Exige nombrar al experto/asesor para no confundirlo con un "no quiero eso"."""
    raw = message.strip().lower()
    return any(n in raw for n in _NEGATIONS) and any(w in raw for w in _EXPERT_WORDS)


def is_expert_entry_message(message: str) -> bool:
    raw = message.strip().lower()
    if raw == EXPERT_ENTRY_ID:
        return True
    if any(negacion in raw for negacion in _NEGATIONS):
        return False  # "no necesito un asesor" no es un pedido
    return any(phrase in raw for phrase in _ENTRY_PHRASES)


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


def _needs_ack(request_id: str) -> bool:
    """Acusar recibo solo al empezar una tanda de mensajes: el primero desde que
    habló el experto (o desde que tomó la solicitud), o tras un rato sin escribir.
    Sin esto el chat queda en silencio y parece que el bot lo ignora."""
    try:
        last = get_last_expert_message(request_id)
    except Exception as error:
        logger.warning("No se pudo consultar el último mensaje del experto: %s", error)
        return False
    if not last or last.get("sender") == "expert":
        return True
    sent = parse_timestamp(last.get("created_at"))
    return bool(sent and datetime.now(timezone.utc) - sent > ACK_AFTER)


def _draft_expired(draft: dict) -> bool:
    created = parse_timestamp(draft.get("created_at"))
    return bool(created and datetime.now(timezone.utc) - created > DRAFT_TTL)


def _handle_draft(draft: dict, message: str, reset_commands: set[str]):
    raw = _clean(message)

    if raw in (CANCEL_ID, "cancelar") or is_cancel_intent(message):
        delete_expert_draft(draft["id"])
        return "Listo, cancelé la solicitud. Puedes pedir un experto otra vez desde el menú cuando quieras."
    if raw in reset_commands or raw in _MENU_WORDS:
        delete_expert_draft(draft["id"])
        return None

    if not draft.get("topic"):
        key = raw[len(TOPIC_PREFIX):] if raw.startswith(TOPIC_PREFIX) else topic_from_text(message)
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


def handle_expert_message(
    user: dict,
    message: str,
    reset_commands: set[str],
    message_id: str | None = None,
):
    """Devuelve la respuesta para el emprendedor (texto o widget), EXPERT_RELAY si
    el mensaje se reenvió al panel (sin respuesta), o None si no aplica y sigue
    el flujo normal. `message_id` es el wamid del mensaje recibido: se guarda
    para que el admin pueda responderlo citándolo."""
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
            if _clean(message) in reset_commands:
                resolve_expert_request(open_request["id"], "emprendedor (reinicio)")
                return None
            if _is_end_command(message):
                touch_user_message(open_request["id"])  # su "terminar" renueva la ventana
                resolve_expert_request(open_request["id"], "emprendedor")
                return (
                    "✅ Terminó la conversación con el experto. Vuelves a hablar "
                    "con el asistente. Si necesitas al experto otra vez, puedes "
                    "pedirlo desde el menú."
                )
            needs_ack = _needs_ack(open_request["id"])
            add_expert_message(open_request["id"], "user", message, wa_message_id=message_id)
            touch_user_message(open_request["id"])
            if is_cancel_intent(message):
                # Se reenvía igual al experto, pero no se cierra nada: puede ser
                # parte de la conversación. Solo se le recuerda cómo salir.
                return (
                    "Tu mensaje le llegó al experto. Si quieres volver a hablar "
                    "con el asistente, escribe *terminar*."
                )
            if needs_ack:
                return (
                    "📨 Le envié tu mensaje al experto. Te responderá por este mismo "
                    "chat. Si quieres volver a hablar con el asistente, escribe *terminar*."
                )
            return EXPERT_RELAY

        if open_request and is_cancel_intent(message):
            resolve_expert_request(open_request["id"], "emprendedor (canceló)")
            return (
                "Listo, cancelé tu solicitud. Puedes pedir un experto otra vez "
                "desde el menú cuando quieras."
            )

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
