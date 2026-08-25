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
    return normalized in RATE_LIMIT_EXEMPT_COMMANDS


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
