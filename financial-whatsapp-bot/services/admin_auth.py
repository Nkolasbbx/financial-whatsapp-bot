"""
FinancIAl — services/admin_auth.py

Login del panel municipal (InnovaRecoleta, El Bosque) y de la cuenta del
equipo FinancIAl. Cuentas fijas — ver ADMIN_ACCOUNTS en config.py — así
que no se construye un sistema de registro/roles todavía. Las contraseñas
viven en variables de entorno, nunca en el código.

La cuenta del equipo (rol "equipo") solo puede usar /admin/documentos: por
defecto get_admin_session_account no la acepta, y solo las rutas de
documentos la habilitan explícitamente con allow_equipo=True.

La sesión se guarda en Redis con su propio prefijo, para no mezclarse con
la sesión del panel del emprendedor (ver services/portal_auth.py).
"""
import logging
import secrets

from config import ADMIN_ACCOUNTS, ADMIN_ROL_EQUIPO

logger = logging.getLogger("financial")

_SESSION_PREFIX = "admin_session:"
_CSRF_PREFIX = "admin_csrf:"
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


def es_equipo(account: dict | None) -> bool:
    return bool(account) and account.get("rol") == ADMIN_ROL_EQUIPO


def comunas_municipales() -> list[str]:
    """Comunas de las cuentas municipales, en el formato con que se guardan
    en ingestion_jobs/documents (ej. "Recoleta", "El Bosque")."""
    return [
        account["comuna"]
        for account in ADMIN_ACCOUNTS.values()
        if not es_equipo(account)
    ]


async def get_admin_session_account(
    redis,
    session_id: str | None,
    allow_equipo: bool = False,
) -> dict | None:
    """Devuelve la cuenta asociada a una sesión de admin válida, o None.

    La cuenta del equipo FinancIAl solo se devuelve con allow_equipo=True:
    el resto del panel (emprendedores, solicitudes, convocatorias) trabaja
    con datos de una comuna y no aplica a ella."""
    if not session_id:
        return None
    username = _as_str(await redis.get(_SESSION_PREFIX + session_id))
    if not username:
        return None
    account = ADMIN_ACCOUNTS.get(username)
    if es_equipo(account) and not allow_equipo:
        return None
    return account


async def destroy_admin_session(redis, session_id: str | None) -> None:
    if session_id:
        await redis.delete(_SESSION_PREFIX + session_id)


async def get_or_create_admin_csrf_token(redis, session_id: str) -> str:
    """Token CSRF de la API JSON del panel (HdU019), mismo patrón que
    portal_auth.get_or_create_csrf_token pero con prefijo propio."""
    if not session_id:
        raise ValueError("Se necesita una sesión válida")

    key = _CSRF_PREFIX + session_id
    existing = await redis.get(key)
    if existing:
        return _as_str(existing)

    token = secrets.token_urlsafe(32)
    await redis.set(key, token, ex=SESSION_TTL_SECONDS)
    return token


async def validate_admin_csrf_token(
    redis,
    session_id: str | None,
    submitted_token: str | None,
) -> bool:
    if not session_id or not submitted_token:
        return False

    stored_token = await redis.get(_CSRF_PREFIX + session_id)
    if not stored_token:
        return False

    return secrets.compare_digest(_as_str(stored_token), submitted_token)
