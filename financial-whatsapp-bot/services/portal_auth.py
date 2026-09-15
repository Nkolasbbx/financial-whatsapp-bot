"""
FinancIAl — services/portal_auth.py

Login sin contraseña para el panel web del emprendedor: un "magic link"
que el bot manda por WhatsApp. Quien tiene acceso al WhatsApp del negocio
es quien puede entrar — no se pide ni guarda ningún dato nuevo, la
identidad sigue siendo el mismo teléfono que ya usa con el bot.

Todo vive en Redis con vencimiento, igual que el lock de teléfono (ver
phone_lock.py): un token de acceso de un solo uso para el link, y una
sesión de duración más larga una vez que ese token se valida.
"""
import logging
import secrets

logger = logging.getLogger("financial")

_TOKEN_PREFIX = "portal_token:"
_SESSION_PREFIX = "portal_session:"
_CSRF_PREFIX = "portal_csrf:"

TOKEN_TTL_SECONDS = 30 * 60          # 30 minutos para hacer clic en el link
SESSION_TTL_SECONDS = 7 * 24 * 3600  # 7 días de sesión después de entrar


def _as_str(value) -> str | None:
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else value


async def create_access_token(redis, phone: str) -> str:
    """Genera un token de un solo uso para el link de acceso al panel."""
    token = secrets.token_urlsafe(32)
    await redis.set(_TOKEN_PREFIX + token, phone, ex=TOKEN_TTL_SECONDS)
    return token


async def redeem_access_token(redis, token: str) -> str | None:
    """Valida un token de acceso y lo invalida (uso único).

    Devuelve el teléfono asociado, o None si el token no existe, ya venció
    o ya se usó antes.
    """
    key = _TOKEN_PREFIX + token
    phone = await redis.get(key)
    if phone is None:
        return None

    await redis.delete(key)
    return _as_str(phone)


async def create_session(redis, phone: str) -> str:
    """Crea una sesión de panel web para un teléfono ya verificado."""
    session_id = secrets.token_urlsafe(32)
    await redis.set(_SESSION_PREFIX + session_id, phone, ex=SESSION_TTL_SECONDS)
    return session_id


async def get_session_phone(redis, session_id: str | None) -> str | None:
    """Devuelve el teléfono asociado a una sesión válida, o None."""
    if not session_id:
        return None
    phone = await redis.get(_SESSION_PREFIX + session_id)
    return _as_str(phone)


async def get_or_create_csrf_token(redis, session_id: str) -> str:
    """Obtiene el token CSRF asociado a una sesión o crea uno nuevo."""
    if not session_id:
        raise ValueError("Se necesita una sesión válida")

    key = _CSRF_PREFIX + session_id
    existing = await redis.get(key)
    if existing:
        return _as_str(existing)

    token = secrets.token_urlsafe(32)
    await redis.set(key, token, ex=SESSION_TTL_SECONDS)
    return token


async def validate_csrf_token(
    redis,
    session_id: str | None,
    submitted_token: str | None,
) -> bool:
    """Compara de forma segura el token CSRF de una sesión del portal."""
    if not session_id or not submitted_token:
        return False

    stored_token = await redis.get(_CSRF_PREFIX + session_id)
    if not stored_token:
        return False

    return secrets.compare_digest(
        _as_str(stored_token),
        submitted_token,
    )
