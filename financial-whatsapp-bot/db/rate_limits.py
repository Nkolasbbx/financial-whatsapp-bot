import logging

from config import (
    RATE_LIMIT_BLOCK_SECONDS,
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_MAX_MESSAGES,
    RATE_LIMIT_WINDOW_SECONDS,
)

logger = logging.getLogger("financial")

RATE_LIMIT_WARNING = (
    "⏳ Has enviado varios mensajes en muy poco tiempo.\n\n"
    "Para evitar sobrecargar el asistente, pausaremos tus consultas "
    "durante 1 minuto. Después podrás continuar normalmente."
)

# Estos comandos deben seguir disponibles para que el usuario pueda revocar
# su consentimiento aunque tenga un bloqueo temporal activo.
RATE_LIMIT_EXEMPT_COMMANDS = {
    "pausar recordatorios",
    "desactivar recordatorios",
    "no quiero recordatorios",
    "menu_recordatorios_off",
}


def _allow_request() -> dict:
    return {
        "allowed": True,
        "notify_user": False,
        "retry_after_seconds": 0,
    }


def is_rate_limit_exempt(message: str) -> bool:
    """Indica si el mensaje debe poder procesarse aun durante un bloqueo."""
    normalized = (message or "").strip().lower().rstrip(".")
    return (
        normalized in RATE_LIMIT_EXEMPT_COMMANDS
        or normalized == "menu_finances"
        or normalized.startswith("finance_")
    )


def check_message_rate_limit(phone: str) -> dict:
    """Consulta en Supabase si el teléfono puede enviar otro mensaje.

    Ante un fallo del limitador se permite continuar para no bloquear a todos
    los usuarios por una caída temporal de la dependencia.
    """
    import dependencies

    if not RATE_LIMIT_ENABLED:
        return _allow_request()

    if dependencies.supabase_admin is None:
        logger.error(
            "Rate limit no disponible: SUPABASE_SERVICE_ROLE_KEY no está "
            "configurada"
        )
        return _allow_request()

    try:
        result = dependencies.supabase_admin.rpc(
            "check_message_rate_limit",
            {
                "p_phone": phone,
                "p_max_messages": RATE_LIMIT_MAX_MESSAGES,
                "p_window_seconds": RATE_LIMIT_WINDOW_SECONDS,
                "p_block_seconds": RATE_LIMIT_BLOCK_SECONDS,
            },
        ).execute()

        data = result.data
        if isinstance(data, list):
            decision = data[0] if data else None
        elif isinstance(data, dict):
            decision = data
        else:
            decision = None

        if not decision:
            logger.error(
                "La función check_message_rate_limit no devolvió una decisión"
            )
            return _allow_request()

        return {
            "allowed": bool(decision.get("allowed")),
            "notify_user": bool(decision.get("notify_user")),
            "retry_after_seconds": int(
                decision.get("retry_after_seconds") or 0
            ),
        }
    except Exception as error:
        logger.exception(
            "No se pudo consultar el rate limit para %s: %s",
            phone,
            error,
        )
        return _allow_request()


# ── Rate limit en Redis (HDU optimización, P-07) ──
# Misma semántica que la RPC check_message_rate_limit:
#   * con un bloqueo vigente, rechaza sin volver a avisar;
#   * si el teléfono ya tiene max mensajes en la ventana, bloquea y avisa una vez;
#   * si no, registra el mensaje y lo deja pasar.
# El reloj lo entrega el backend (ms) para que el script sea determinista.
_REDIS_RATE_LIMIT_SCRIPT = """
local ts_key = KEYS[1]
local block_key = KEYS[2]
local now = tonumber(ARGV[1])
local max_messages = tonumber(ARGV[2])
local window_ms = tonumber(ARGV[3])
local block_ms = tonumber(ARGV[4])
local member = ARGV[5]

local blocked_until = tonumber(redis.call('GET', block_key) or '0')
if blocked_until > now then
    return {0, 0, math.max(1, math.ceil((blocked_until - now) / 1000))}
end

redis.call('ZREMRANGEBYSCORE', ts_key, '-inf', now - window_ms)
if redis.call('ZCARD', ts_key) >= max_messages then
    redis.call('SET', block_key, now + block_ms, 'PX', block_ms)
    return {0, 1, math.ceil(block_ms / 1000)}
end

redis.call('ZADD', ts_key, now, member)
redis.call('PEXPIRE', ts_key, window_ms + block_ms)
return {1, 0, 0}
"""

_REDIS_KEY_PREFIX = "rate_limit:"


async def check_message_rate_limit_redis(redis, phone: str) -> dict:
    """Versión en Redis del limitador: una operación atómica, sin Postgres.

    Ante un fallo de Redis se permite continuar, igual que la versión RPC.
    """
    import time
    import uuid

    if not RATE_LIMIT_ENABLED:
        return _allow_request()

    try:
        now_ms = int(time.time() * 1000)
        result = await redis.eval(
            _REDIS_RATE_LIMIT_SCRIPT,
            2,
            f"{_REDIS_KEY_PREFIX}{phone}:ts",
            f"{_REDIS_KEY_PREFIX}{phone}:blocked",
            now_ms,
            RATE_LIMIT_MAX_MESSAGES,
            RATE_LIMIT_WINDOW_SECONDS * 1000,
            RATE_LIMIT_BLOCK_SECONDS * 1000,
            f"{now_ms}-{uuid.uuid4().hex}",
        )
        allowed, notify_user, retry_after = (int(value) for value in result)
        return {
            "allowed": bool(allowed),
            "notify_user": bool(notify_user),
            "retry_after_seconds": retry_after,
        }
    except Exception as error:
        logger.error(
            "No se pudo consultar el rate limit en Redis (%s); se permite el mensaje",
            type(error).__name__,
        )
        return _allow_request()
