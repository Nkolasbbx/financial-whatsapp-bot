"""
FinancIAl — services/audio_consent.py

Consentimiento para procesar audios y eco de lo que se entendió (HdU011,
iteración). La primera vez que alguien manda un audio se le explica cómo se
procesa y se le pide aceptar ANTES de transcribirlo; mientras decide, el audio
queda esperando en Redis y se procesa si acepta. Después, cada transcripción se
muestra como "Esto entendí de tu audio" con un botón para decir que no es eso.
"""
import logging

logger = logging.getLogger("financial")

ACCEPT_ID = "audio_acepto"
REJECT_ID = "audio_rechazo"
WRONG_ID = "audio_incorrecto"

_ACCEPT_COMMANDS = {ACCEPT_ID, "aceptar audios", "acepto audios"}
_REJECT_COMMANDS = {REJECT_ID}
_WRONG_COMMANDS = {WRONG_ID}
# Decirlo (por voz o escrito) vale igual que tocar el botón "No es lo que dije".
_WRONG_PHRASES = (
    "no es lo que dije", "no fue lo que dije", "no dije eso", "eso no es lo que dije",
)

_PENDING_PREFIX = "audio_consent_pending:"
_PENDING_TTL_SECONDS = 60 * 60

CONSENT_BODY = (
    "🎙️ *Antes de usar tus audios*\n\n"
    "Para responderte, voy a convertir tu audio en texto y usar ese texto para "
    "entender tu consulta. El audio no se utilizará para clonar ni imitar tu voz, "
    "y FinancIAl no lo guarda.\n\n"
    "Siempre te voy a mostrar lo que entendí para que puedas corregirlo.\n\n"
    "¿Aceptas que procese tus audios así? Si no aceptas, puedes seguir usando "
    "el asistente por texto."
)

ACCEPTED_TEXT = "✅ Gracias, ya puedes mandarme audios."
ACCEPTED_AND_PROCESSING_TEXT = "✅ Gracias, proceso tu audio ahora."
REJECTED_TEXT = (
    "Entendido, no voy a procesar tus audios. Puedes seguir escribiéndome por "
    "texto. Si cambias de idea, escribe *aceptar audios*."
)
DECLINED_REMINDER_TEXT = (
    "Por ahora no proceso audios porque no aceptaste las condiciones. "
    "Escríbeme tu consulta por texto, o escribe *aceptar audios* si quieres "
    "activarlos."
)
WRONG_TEXT = (
    "Entendido, descarté ese audio y no hice nada con él. Puedes enviarlo de "
    "nuevo o escribirme lo que necesitabas ✍️"
)


def consent_state(user: dict | None) -> bool | None:
    """True/False si ya decidió; None si todavía no se le ha preguntado."""
    if not user:
        return None
    value = user.get("audio_consent")
    return value if isinstance(value, bool) else None


def consent_prompt() -> dict:
    return {
        "type": "buttons",
        "body": CONSENT_BODY,
        "options": [(ACCEPT_ID, "✅ Acepto"), (REJECT_ID, "No acepto")],
    }


def echo_widget(transcript: str) -> dict:
    """La transcripción, siempre, antes de responder (con salida si está mal)."""
    return {
        "type": "buttons",
        "body": f'📝 *Esto entendí de tu audio:*\n"{transcript}"',
        "options": [(WRONG_ID, "❌ No es lo que dije")],
    }


def audio_choice(message: str | None) -> str | None:
    """'accept', 'reject' o 'wrong' si el mensaje es una respuesta de audio."""
    raw = (message or "").strip().lower().rstrip(".!")
    if raw in _ACCEPT_COMMANDS:
        return "accept"
    if raw in _REJECT_COMMANDS:
        return "reject"
    if raw in _WRONG_COMMANDS or any(phrase in raw for phrase in _WRONG_PHRASES):
        return "wrong"
    return None


async def stash_pending_audio(redis, phone: str, media_id: str) -> None:
    await redis.set(_PENDING_PREFIX + phone, media_id, ex=_PENDING_TTL_SECONDS)


async def pop_pending_audio(redis, phone: str) -> str | None:
    key = _PENDING_PREFIX + phone
    value = await redis.get(key)
    if value is None:
        return None
    await redis.delete(key)
    return value.decode() if isinstance(value, bytes) else value
