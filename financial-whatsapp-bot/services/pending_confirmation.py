"""
FinancIAl — services/pending_confirmation.py

Confirmación de una sola pregunta para comandos que llegan por audio y
modificarían datos del usuario — hoy solo "reiniciar" (HdU11, AC3). Vive
en Redis con TTL corto, mismo patrón que phone_lock.py / portal_auth.py.

Solo se usa para el camino de audio: un comando destructivo escrito o
tocado como botón se sigue ejecutando directo (ver services/message_router.py),
porque ahí ya hay una acción explícita del usuario.
"""
import logging

logger = logging.getLogger("financial")

_PREFIX = "pending_confirm:"
_TTL_SECONDS = 5 * 60

_AFFIRMATIVE = {
    "si", "sí", "sip", "s", "confirmo", "confirmar",
    "dale", "ok", "okay", "de acuerdo", "correcto",
}
_NEGATIVE = {"no", "cancelar", "cancela", "mejor no", "n"}


def _as_str(value) -> str | None:
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else value


async def set_pending_confirmation(redis, phone: str, action: str) -> None:
    await redis.set(_PREFIX + phone, action, ex=_TTL_SECONDS)


async def get_pending_confirmation(redis, phone: str) -> str | None:
    return _as_str(await redis.get(_PREFIX + phone))


async def clear_pending_confirmation(redis, phone: str) -> None:
    await redis.delete(_PREFIX + phone)


_TRAILING_PUNCTUATION = " .,!?¡¿;:\"'"


def _normalize(message: str) -> str:
    # Una transcripción de audio real ("Sí." en vez de "sí") suele traer
    # puntuación/mayúsculas de más que un mensaje escrito no tendría.
    return (message or "").strip().lower().strip(_TRAILING_PUNCTUATION)


def is_affirmative(message: str) -> bool:
    return _normalize(message) in _AFFIRMATIVE


def is_negative(message: str) -> bool:
    return _normalize(message) in _NEGATIVE
