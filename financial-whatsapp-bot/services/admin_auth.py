"""
FinancIAl — services/admin_auth.py

Login del panel municipal (InnovaRecoleta, El Bosque). Solo 2 cuentas
fijas por ahora — ver ADMIN_ACCOUNTS en config.py — así que no se
construye un sistema de registro/roles todavía. Las contraseñas viven en
variables de entorno, nunca en el código.

La sesión se guarda en Redis con su propio prefijo, para no mezclarse con
la sesión del panel del emprendedor (ver services/portal_auth.py).
"""
import logging
import secrets

from config import ADMIN_ACCOUNTS

logger = logging.getLogger("financial")

_SESSION_PREFIX = "admin_session:"
SESSION_TTL_SECONDS = 24 * 3600  # sesión de admin, más corta que la del emprendedor


def _as_str(value) -> str | None:
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else value


def authenticate_admin(username: str, password: str) -> dict | None:
    """Valida usuario/contraseña contra las cuentas fijas.

    Devuelve la cuenta (comuna, nombre) si es válida, o None. Usa
    comparación de tiempo constante para no filtrar información por
    timing, aunque hoy solo haya 2 cuentas.
    """
    account = ADMIN_ACCOUNTS.get((username or "").strip().lower())
    if not account or not account.get("password"):
        return None

    if not secrets.compare_digest(password or "", account["password"]):
        return None

    return account


async def create_admin_session(redis, username: str) -> str:
    session_id = secrets.token_urlsafe(32)
    await redis.set(_SESSION_PREFIX + session_id, username, ex=SESSION_TTL_SECONDS)
    return session_id


async def get_admin_session_account(redis, session_id: str | None) -> dict | None:
    """Devuelve la cuenta asociada a una sesión de admin válida, o None."""
    if not session_id:
        return None
    username = _as_str(await redis.get(_SESSION_PREFIX + session_id))
    if not username:
        return None
    return ADMIN_ACCOUNTS.get(username)


async def destroy_admin_session(redis, session_id: str | None) -> None:
    if session_id:
        await redis.delete(_SESSION_PREFIX + session_id)
