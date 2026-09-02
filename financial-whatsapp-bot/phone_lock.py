import asyncio
import logging
import uuid

logger = logging.getLogger("financial")

_LOCK_KEY_PREFIX = "phone_lock:"

# Un poco más que job_timeout (120s en WorkerSettings) para que un job de IA
# colgado no deje el teléfono bloqueado más tiempo del que el propio worker
# ya lo daría por muerto.
LOCK_TTL_SECONDS = 130
_POLL_INTERVAL_SECONDS = 0.3

# Compara el token antes de borrar: si nuestro lock ya expiró por TTL y otro
# proceso tomó uno nuevo para el mismo teléfono, no queremos borrar el suyo.
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


async def acquire_phone_lock(redis, phone: str) -> str:
    """Adquiere el lock de orden de respuesta para un teléfono.

    Si ya está tomado (ej. un job de IA todavía en curso para ese mismo
    número), espera con polling hasta que se libere. El TTL garantiza que
    nunca queda tomado más de LOCK_TTL_SECONDS, aunque quien lo tomó se
    caiga sin liberarlo explícitamente.
    """
    key = _LOCK_KEY_PREFIX + phone
    token = uuid.uuid4().hex
    while True:
        acquired = await redis.set(key, token, nx=True, ex=LOCK_TTL_SECONDS)
        if acquired:
            return token
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


async def release_phone_lock(redis, phone: str, token: str) -> None:
    """Libera el lock solo si todavía nos pertenece (compara el token)."""
    key = _LOCK_KEY_PREFIX + phone
    try:
        await redis.eval(_RELEASE_SCRIPT, 1, key, token)
    except Exception as error:
        logger.error("No se pudo liberar el lock de teléfono %s: %s", phone, error)
