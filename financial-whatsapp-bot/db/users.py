import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("financial")

# IN-MEMORY STORAGE
# Se utiliza como fallback cuando Supabase no está configurado.
users_db: dict[str, dict] = {}

# bloqueado se preserva: reiniciar el perfil no debe desbloquear al usuario.
_RESET_PRESERVED_FIELDS = {"id", "phone", "auth_user_id", "created_at", "bloqueado"}
_RESET_SAFE_DEFAULTS = {
    "onboarding_step": 0,
    "reminders_enabled": False,
    "reminders_paused": False,
    "reminder_count": 0,
}


class UserPersistenceError(RuntimeError):
    """No se pudo leer o guardar el perfil de forma persistente."""


_ONBOARDING_FIELDS = {
    "nombre_completo", "rut", "email", "direccion_negocio", "profile_completion_step",
    "rubro", "rubro_raw", "comuna", "inicio_sii", "onboarding_step",
    "roadmap", "roadmap_completed_at", "created_at",
}


def _profile_client():
    import dependencies

    client = dependencies.supabase
    if client is None and (
        getattr(dependencies, "SUPABASE_URL", None)
        or getattr(dependencies, "SUPABASE_KEY", None)
    ):
        raise UserPersistenceError("Supabase está configurado pero su cliente no está disponible")
    return client


def get_user(phone: str, *, strict: bool = False) -> dict | None:
    """Obtiene el perfil de un usuario mediante su número telefónico."""
    import dependencies

    client = _profile_client() if strict else dependencies.supabase
    if client:
        try:
            result = (
                client
                .table("users")
                .select("*")
                .eq("phone", phone)
                .limit(1)
                .execute()
            )

            if result.data:
                user = result.data[0]

                # La columna roadmap es JSONB, pero se mantiene compatibilidad
                # en caso de que llegue como texto JSON.
                if isinstance(user.get("roadmap"), str):
                    try:
                        user["roadmap"] = json.loads(user["roadmap"])
                    except json.JSONDecodeError:
                        logger.warning(
                            "El roadmap del usuario %s no contiene JSON válido",
                            phone,
                        )
                        user["roadmap"] = None

                return user

            if strict:
                return None

        except Exception as error:
            if strict:
                logger.error("No se pudo consultar el perfil (%s)", type(error).__name__)
                raise UserPersistenceError("No se pudo consultar el perfil") from error
            logger.error("Supabase get_user error: %s", error)

    return users_db.get(phone)


BLOCKED_USER_MESSAGE = (
    "El bot de momento no se encuentra disponible, para solicitar acceso "
    "envía un mensaje a grupofinancial06@gmail.com"
)


def is_user_blocked(phone: str) -> bool:
    """Indica si el usuario fue bloqueado manualmente (columna users.bloqueado).

    Un teléfono sin perfil no está bloqueado. Ante un fallo de Supabase se
    permite continuar para no dejar a todos sin bot por una caída temporal.
    """
    import dependencies

    client = getattr(dependencies, "supabase_admin", None) or dependencies.supabase
    if client:
        try:
            result = (
                client
                .table("users")
                .select("bloqueado")
                .eq("phone", phone)
                .limit(1)
                .execute()
            )
            return bool(result.data and result.data[0].get("bloqueado"))
        except Exception as error:
            logger.error("No se pudo consultar el bloqueo (%s)", type(error).__name__)
            return False

    return bool((users_db.get(phone) or {}).get("bloqueado"))


def create_user_profile(phone: str) -> dict:
    """Crea la identidad inicial sin sobrescribir un perfil existente."""
    client = _profile_client()
    now = datetime.now(timezone.utc).isoformat()
    data = {"phone": phone, "onboarding_step": "0", "created_at": now, "updated_at": now}
    if client:
        try:
            result = (
                client.table("users")
                .upsert(data, on_conflict="phone", ignore_duplicates=True)
                .execute()
            )
            if result.data:
                return result.data[0]
            # Otro proceso pudo crear el perfil entre la lectura y el insert.
            existing = get_user(phone, strict=True)
            if existing:
                return existing
        except Exception as error:
            logger.error("No se pudo crear el perfil (%s)", type(error).__name__)
            raise UserPersistenceError("No se pudo crear el perfil") from error
        raise UserPersistenceError("La creación del perfil no devolvió un registro")
    # Modo local sin Supabase configurado; nunca se usa tras un error de red.
    return users_db.setdefault(phone, data)


def update_onboarding_profile(phone: str, changes: dict) -> dict:
    """Guarda únicamente el avance de onboarding, sin reemplazar el resto del usuario."""
    if not changes or not set(changes).issubset(_ONBOARDING_FIELDS):
        raise ValueError("Campos de onboarding no permitidos")
    client = _profile_client()
    payload = {**changes, "updated_at": datetime.now(timezone.utc).isoformat()}
    if "onboarding_step" in payload:
        payload["onboarding_step"] = str(payload["onboarding_step"])
    if client:
        try:
            result = (
                client.table("users")
                .update(payload).eq("phone", phone).execute()
            )
            if result.data:
                return result.data[0]
        except Exception as error:
            # Los errores de Postgres pueden contener la fila completa: no registrar PII.
            logger.error("No se pudo guardar el onboarding (%s)", type(error).__name__)
            raise UserPersistenceError("No se pudo guardar el onboarding") from error
        raise UserPersistenceError("La actualización no devolvió un perfil")
    if phone not in users_db:
        raise UserPersistenceError("El perfil no existe")
    users_db[phone] = {**users_db[phone], **payload}
    return dict(users_db[phone])


def get_user_id(phone: str) -> str | None:
    """Obtiene el UUID interno de un usuario mediante su teléfono."""
    import dependencies

    if dependencies.supabase:
        try:
            result = (
                dependencies.supabase
                .table("users")
                .select("id")
                .eq("phone", phone)
                .limit(1)
                .execute()
            )

            if result.data:
                return result.data[0]["id"]

        except Exception as error:
            logger.error("Supabase get_user_id error: %s", error)

    user = users_db.get(phone)

    if user:
        return user.get("id")

    return None


def save_user(phone: str, data: dict) -> dict | None:
    """Crea o actualiza el perfil de un usuario."""
    import dependencies

    db_data = {
        **data,
        "phone": phone,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Este campo se utiliza solamente en memoria o en la lógica de la app.
    # No existe en la tabla users.
    db_data.pop("conversation_history", None)

    # roadmap es JSONB en PostgreSQL.
    # Los diccionarios y listas se envían directamente a Supabase.
    roadmap = db_data.get("roadmap")

    if isinstance(roadmap, str):
        try:
            db_data["roadmap"] = json.loads(roadmap)
        except json.JSONDecodeError:
            logger.warning(
                "No se guardó un roadmap inválido para el usuario %s",
                phone,
            )
            db_data.pop("roadmap", None)

    elif roadmap is not None and not isinstance(
        roadmap,
        (dict, list, int, float, bool),
    ):
        logger.warning(
            "Tipo de roadmap no compatible para el usuario %s: %s",
            phone,
            type(roadmap).__name__,
        )
        db_data.pop("roadmap", None)

    if dependencies.supabase:
        try:
            result = (
                dependencies.supabase
                .table("users")
                .upsert(db_data, on_conflict="phone")
                .execute()
            )

            if result.data:
                return result.data[0]

            return None

        except Exception as error:
            # El perfil incluye RUT/correo: una excepción puede incluir la fila.
            logger.error("Supabase save_user error (%s)", type(error).__name__)

    users_db[phone] = db_data
    return db_data


def reset_user_profile(phone: str, current_user: dict) -> dict | None:
    """Limpia el estado funcional y conserva la identidad del usuario.

    Las columnas se obtienen del propio registro para que futuras columnas
    funcionales también se reinicien sin mantener una lista duplicada. Los
    campos de control que pueden ser NOT NULL reciben valores iniciales
    seguros en lugar de SQL NULL.
    """
    import dependencies

    reset_payload = {
        column: None
        for column in current_user
        if column not in _RESET_PRESERVED_FIELDS
        and column != "conversation_history"
    }
    for column, value in _RESET_SAFE_DEFAULTS.items():
        if column in current_user or column == "onboarding_step":
            reset_payload[column] = value
    if "updated_at" in current_user:
        reset_payload["updated_at"] = datetime.now(timezone.utc).isoformat()

    client = dependencies.supabase_admin or dependencies.supabase
    if client:
        try:
            result = (
                client.table("users")
                .update(reset_payload)
                .eq("phone", phone)
                .execute()
            )
            if result.data:
                users_db.pop(phone, None)
                return result.data[0]
            return None
        except Exception as error:
            logger.error("Supabase reset_user_profile error: %s", error)
            return None

    reset_user = {
        field: current_user.get(field)
        for field in _RESET_PRESERVED_FIELDS
        if current_user.get(field) is not None
    }
    reset_user.update(reset_payload)
    reset_user["phone"] = phone
    users_db[phone] = reset_user
    return reset_user


def save_message(
    phone: str,
    role: str,
    content: str,
    channel: str = "whatsapp",
) -> None:
    """Guarda un mensaje asociado al UUID interno del usuario."""
    import dependencies

    if not dependencies.supabase:
        logger.warning(
            "No se guardó el mensaje porque Supabase no está configurado"
        )
        return

    try:
        user_id = get_user_id(phone)

        if not user_id:
            logger.error(
                "No se pudo guardar el mensaje: no existe un usuario "
                "asociado al teléfono %s",
                phone,
            )
            return

        dependencies.supabase.table("messages").insert({
            "user_id": user_id,
            "phone": phone,  # Se mantiene temporalmente por compatibilidad.
            "role": role,
            "channel": channel,
            "content": content,
        }).execute()

    except Exception as error:
        logger.error("Supabase save_message error: %s", error)


def get_messages(phone: str, limit: int = 12) -> list[dict]:
    """Obtiene los últimos mensajes del usuario en orden cronológico."""
    import dependencies

    if not dependencies.supabase:
        return []

    try:
        user_id = get_user_id(phone)

        if not user_id:
            logger.warning(
                "No se encontraron mensajes porque no existe el usuario %s",
                phone,
            )
            return []

        result = (
            dependencies.supabase
            .table("messages")
            .select("role, content")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )

        messages = result.data or []

        # La consulta trae primero los mensajes más recientes.
        # Se invierten para entregarlos en orden cronológico.
        return list(reversed(messages))

    except Exception as error:
        logger.error("Supabase get_messages error: %s", error)
        return []


def get_last_user_message(phone: str) -> str | None:
    """Obtiene solamente el mensaje de usuario más reciente."""
    import dependencies

    if not dependencies.supabase:
        return None

    try:
        user_id = get_user_id(phone)

        if not user_id:
            logger.warning(
                "No se encontró el último mensaje porque no existe el usuario %s",
                phone,
            )
            return None

        result = (
            dependencies.supabase
            .table("messages")
            .select("content")
            .eq("user_id", user_id)
            .eq("role", "user")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if not result.data:
            return None

        return result.data[0].get("content")

    except Exception as error:
        logger.error("Supabase get_last_user_message error: %s", error)
        return None


def contar_mensajes(phone: str) -> int:
    """Cuenta el total de mensajes asociados a un usuario."""
    import dependencies

    if not dependencies.supabase:
        return 0

    try:
        user_id = get_user_id(phone)

        if not user_id:
            return 0

        result = (
            dependencies.supabase
            .table("messages")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .execute()
        )

        return result.count or 0

    except Exception as error:
        logger.error("Supabase contar_mensajes error: %s", error)
        return 0


def get_users_by_comuna(comuna: str) -> list[dict]:
    """Todos los usuarios con onboarding completado en una comuna dada.

    Usa el cliente administrativo (bypassa RLS) porque esto alimenta el
    dashboard municipal — una consulta agregada sobre todos los usuarios,
    no la de un usuario sobre sí mismo.
    """
    import dependencies

    if dependencies.supabase_admin is None:
        logger.error(
            "No se pudo consultar usuarios por comuna: falta SUPABASE_SERVICE_ROLE_KEY"
        )
        return []

    try:
        result = (
            dependencies.supabase_admin
            .table("users")
            .select(
                "phone, rubro, comuna, inicio_sii, onboarding_step, "
                "roadmap, roadmap_completed_at, created_at"
            )
            .eq("comuna", comuna)
            .eq("onboarding_step", "done")
            .execute()
        )
        usuarios = result.data or []

        # roadmap es JSONB, pero Supabase a veces lo entrega como texto sin
        # parsear (mismo caso que get_user()) — se normaliza acá también.
        for usuario in usuarios:
            if isinstance(usuario.get("roadmap"), str):
                try:
                    usuario["roadmap"] = json.loads(usuario["roadmap"])
                except json.JSONDecodeError:
                    logger.warning(
                        "El roadmap del usuario %s no contiene JSON válido",
                        usuario.get("phone"),
                    )
                    usuario["roadmap"] = []

        return usuarios
    except Exception as error:
        logger.error("Supabase get_users_by_comuna error: %s", error)
        return []
