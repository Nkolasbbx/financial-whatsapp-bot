import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("financial")

# IN-MEMORY STORAGE
# Se utiliza como fallback cuando Supabase no está configurado.
users_db: dict[str, dict] = {}

_RESET_PRESERVED_FIELDS = {"id", "phone", "auth_user_id", "created_at"}
_RESET_SAFE_DEFAULTS = {
    "onboarding_step": 0,
    "reminders_enabled": False,
    "reminders_paused": False,
    "reminder_count": 0,
}


def get_user(phone: str) -> dict | None:
    """Obtiene el perfil de un usuario mediante su número telefónico."""
    import dependencies

    if dependencies.supabase:
        try:
            result = (
                dependencies.supabase
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

        except Exception as error:
            logger.error("Supabase get_user error: %s", error)

    return users_db.get(phone)


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
            logger.error("Supabase save_user error: %s", error)

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
