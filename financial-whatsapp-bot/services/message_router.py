import logging

from db.users import get_user, save_user
from core.roadmaps import (
    get_roadmap_text,
    mark_hito_done,
    revert_last_hito,
    get_pending_milestone,
    HITO_LISTO_ID,
    HITO_AYUDA_ID,
    HITO_VOLVER_ID,
)
from core.fondos import simulate_funds
from core.onboarding import process_onboarding
from db.reminders import (
    disable_reminders,
    enable_reminders,
    record_incoming_reminder_reply,
    record_roadmap_activity,
)

logger = logging.getLogger("financial")

# ids usados por el menú interactivo (lista de Meta). Cada id se trata
# como equivalente a su comando de texto correspondiente.
MENU_OPTIONS = [
    ("menu_roadmap", "📋 Mi roadmap"),
    ("menu_listo", "✅ Marcar hito listo"),
    ("menu_fondo", "🎯 Postular a fondo"),
    ("menu_recordatorios_on", "🔔 Activar recordatorios"),
    ("menu_recordatorios_off", "🔕 Pausar recordatorios"),
    ("menu_reiniciar", "🔄 Reiniciar"),
]


def _menu_widget() -> dict:
    opciones_texto = "\n".join(f"• {label}" for _, label in MENU_OPTIONS)
    body = (
        "📱 *Menú de FinancIAl*\n\n"
        "Estas son tus opciones:\n\n"
        f"{opciones_texto}\n\n"
        "Tócalas en la lista de abajo, o simplemente *escribe tu pregunta* "
        "y te respondo con IA 🤖"
    )
    return {
        "type": "list",
        "body": body,
        "button_text": "Ver opciones",
        "options": MENU_OPTIONS,
    }


def _record_reply_safely(phone: str, reply_to_message_id: str | None) -> bool:
    try:
        return record_incoming_reminder_reply(phone, reply_to_message_id)
    except Exception as error:
        logger.error("No se pudo registrar la respuesta al recordatorio: %s", error)
        return False


def _record_activity_safely(phone: str) -> None:
    try:
        record_roadmap_activity(phone)
    except Exception as error:
        logger.error("No se pudo registrar la actividad del roadmap: %s", error)


def route_message(
    phone: str,
    message: str,
    reply_to_message_id: str | None = None,
):
    """Main router: determines what to do with each incoming message.

    Devuelve un str (texto plano), el string "__AI_QUERY__", o un dict
    {"type": "text"|"buttons"|"list", "body": ..., ...} cuando la respuesta
    viene del flujo de onboarding, del menú, o de los botones del roadmap
    (listo, ayuda, deshacer paso).

    Los ids de botones (menu_roadmap, hito_listo, hito_ayuda, hito_volver,
    etc.) se tratan como equivalentes a sus comandos de texto para que
    funcione igual toque el usuario un botón o escriba el comando.
    """
    message = message.strip()
    msg_lower = message.lower()

    # Get or create user
    user = get_user(phone)

    if not user:
        user = {"phone": phone, "onboarding_step": 0}
        save_user(phone, user)

    # ── Onboarding flow ──
    if user.get("onboarding_step") != "done":
        response = process_onboarding(user, message, save_user)
        if response:
            return response

    # ── Reset command ──
    if msg_lower in ["reiniciar", "reset", "empezar de nuevo", "menu_reiniciar"]:
        from db.users import users_db
        users_db.pop(phone, None)
        new_user = {"phone": phone, "onboarding_step": 0}
        save_user(phone, new_user)
        return process_onboarding(new_user, message, save_user)

    activation_commands = {
        "activar recordatorios",
        "acepto recordatorios",
        "reanudar recordatorios",
        "menu_recordatorios_on",
    }
    if msg_lower in activation_commands:
        try:
            reminders_were_enabled = enable_reminders(phone)
        except Exception as error:
            logger.error("No se pudieron activar los recordatorios: %s", error)
            reminders_were_enabled = False
        if not reminders_were_enabled:
            return (
                "No pude activar los recordatorios en este momento. "
                "Inténtalo nuevamente más tarde."
            )
        return (
            "🔔 *Recordatorios activados.*\n\n"
            "Si pasan 3 días sin que avances en tu roadmap, te enviaré un "
            "recordatorio por WhatsApp. Puedes pausarlos cuando quieras "
            "escribiendo *\"pausar recordatorios\"* o desde el menú."
        )

    pause_commands = {
        "pausar recordatorios",
        "desactivar recordatorios",
        "no quiero recordatorios",
        "menu_recordatorios_off",
    }
    if msg_lower.rstrip(".") in pause_commands:
        _record_reply_safely(phone, reply_to_message_id)
        try:
            reminders_were_disabled = disable_reminders(phone)
        except Exception as error:
            logger.error("No se pudieron pausar los recordatorios: %s", error)
            reminders_were_disabled = False
        if not reminders_were_disabled:
            return (
                "No pude pausar los recordatorios en este momento. "
                "Inténtalo nuevamente más tarde."
            )
        return (
            "🔕 *Recordatorios pausados.*\n\n"
            "Puedes volver a activarlos cuando quieras escribiendo "
            "*\"activar recordatorios\"* o desde el menú."
        )

    replied_to_reminder = False
    if reply_to_message_id or int(user.get("reminder_count") or 0) > 0:
        replied_to_reminder = _record_reply_safely(
            phone,
            reply_to_message_id,
        )

    # ── Roadmap commands ──
    roadmap_triggers = ["roadmap", "mi roadmap", "hitos", "qué me falta", "que me falta", "formalizar", "mis pasos", "mi ruta", "menu_roadmap"]
    if any(trigger in msg_lower for trigger in roadmap_triggers):
        _record_activity_safely(phone)
        return get_roadmap_text(user)

    # ── Mark hito done ──
    done_triggers = ["listo", "hecho", "completado", "ya lo hice", "ya está", "ya esta", "siguiente", "menu_listo", HITO_LISTO_ID]
    if any(trigger in msg_lower for trigger in done_triggers):
        response = mark_hito_done(user, save_user)
        _record_activity_safely(phone)
        return response

    # ── Revert last hito (deshacer paso) ──
    if msg_lower == HITO_VOLVER_ID:
        response = revert_last_hito(user, save_user)
        _record_activity_safely(phone)
        return response

    # ── Ayuda contextual del hito → NUEVO: inyecta contexto en IA ──
    if msg_lower == HITO_AYUDA_ID:
        pending_hito = get_pending_milestone(user)
        if pending_hito:
            # Retorna patrón especial para que webhook despache a IA con contexto
            return "__AI_QUERY_WITH_CONTEXT__"
        else:
            return _menu_widget()  # Fallback si no hay hito pendiente

    if replied_to_reminder:
        _record_activity_safely(phone)
        return get_roadmap_text(user)

    # ── Fund simulation ──
    fund_triggers = ["fondo", "postular", "capital semilla", "capital abeja", "sercotec", "corfo", "financiamiento", "menu_fondo"]
    if any(trigger in msg_lower for trigger in fund_triggers):
        return simulate_funds(user)

    # ── Help / menu ──
    if msg_lower in ["ayuda", "help", "menu", "menú", "opciones"]:
        return _menu_widget()
    
    # ── Manejo de insatisfacción ──
    if detect_unsatisfaction(message):
        response = handle_unsatisfaction_response(user)
        _record_reply_safely(phone, reply_to_message_id)
        return response
     
    # ── Manejo de opciones de insatisfacción ──
    unsatisfied_choices = {
        "unsatisfied_reformulate": "unsatisfied_reformulate",
        "unsatisfied_support": "unsatisfied_support",
        "unsatisfied_continue_roadmap": "unsatisfied_continue_roadmap",
    }

    for choice_id in unsatisfied_choices:
        if msg_lower == choice_id:
            response = handle_unsatisfaction_choice(
                phone, choice_id, message, user, save_user
            )
            if response == "__AI_QUERY_WITH_REFORMULATE__":
                return response  # Especial: reformulación con contexto
            else:
                return response
            
    # ── AI Chat (default) ──
    return "__AI_QUERY__"


def split_message(text: str, max_len: int) -> list[str]:
    """Split long message into chunks at paragraph boundaries."""
    if len(text) <= max_len:
        return [text]

    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break

        # Find last newline before limit
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len

        parts.append(text[:split_at])
        text = text[split_at:].lstrip("\n")

    return parts

UNSATISFIED_PATTERNS = {
    # Respuesta no sirvió
    "no me sirvió", "no sirvio", "eso no me sirvió", "eso no sirvio",
    "no me funcionó", "no funciono",
    
    # No entendió
    "sigo sin entender", "no entiendo", "aún tengo dudas", "todavia tengo dudas",
    "me sigue confundiendo", "confundido", "confundida",
    
    # Respuesta incompleta/insatisfactoria
    "eso no fue lo que", "no es lo que", "no era lo que",
    "me sirve", "me ayuda",  # contexto negativo: "no me sirve", "no me ayuda"
    
    # Petición de aclaración
    "puedes explicar mejor", "explica mejor", "más detalles", "mas detalles",
}

def detect_unsatisfaction(message: str) -> bool:
    """
    Detecta si el usuario está insatisfecho con la respuesta del asesor virtual.
    
    Se activa cuando el usuario escribe cosas como:
    - "eso no me sirvió"
    - "sigo sin entender"
    - "no entiendo"
    - "aún tengo dudas"
    - "no me funcionó"
    
    Args:
        message: str con el mensaje del usuario
        
    Returns:
        bool: True si detecta patrón de insatisfacción
    """
    if not message:
        return False
    
    msg_lower = message.lower().strip()
    
    # Búsqueda de patrones (substring match)
    for pattern in UNSATISFIED_PATTERNS:
        if pattern in msg_lower:
            return True
    
    return False

def handle_unsatisfaction_response(user: dict) -> dict:
    """
    Ofrece opciones interactivas cuando el usuario no quedó satisfecho
    con la respuesta del asesor virtual.
    
    Presenta 3 opciones:
    1. 🔄 Reformular: Intenta otra forma de explicar
    2. 👨‍💼 Soporte humano: Ofrece contacto directo
    3. 📋 Continuar roadmap: Vuelve al flujo principal
    
    Args:
        user: dict con datos del usuario (para personalización opcional)
        
    Returns:
        dict {"type": "buttons", "body": ..., "options": [...]}
    """
    rubro_display = user.get("rubro", "tu emprendimiento").capitalize()
    
    body = (
        "Entiendo que no quedó claro. 😊 *¿Qué prefieres hacer?*\n\n"
        "Puedo intentar explicarlo de otra forma, "
        "conectarte con un asesor real, o continuamos con tu roadmap."
    )
    
    return {
        "type": "buttons",
        "body": body,
        "options": [
            ("unsatisfied_reformulate", "🔄 Reformular respuesta"),
            ("unsatisfied_support", "👨‍💼 Hablar con asesor"),
            ("unsatisfied_continue_roadmap", "📋 Continuar roadmap"),
        ],
    }


def handle_unsatisfaction_choice(
    phone: str,
    choice_id: str,
    message: str,
    user: dict,
    save_user_fn,
) -> dict | str:
    """
    Maneja la opción que eligió el usuario cuando estaba insatisfecho.
    
    Args:
        phone: teléfono del usuario
        choice_id: ID de la opción elegida (unsatisfied_*)
        message: mensaje original del usuario (para "reformular")
        user: dict con datos del usuario
        save_user_fn: función para guardar usuario
        
    Returns:
        dict (para botones/listas) o str "__AI_QUERY_WITH_REFORMULATE__" (para IA)
    """
    
    if choice_id == "unsatisfied_reformulate":
        # Marca que debe reformularse en la IA
        user["last_unsatisfied_message"] = message
        user["reformulate_attempt"] = (user.get("reformulate_attempt", 0) or 0) + 1
        save_user_fn(phone, user)
        
        # Retorna patrón especial para que webhook despache a IA con instrucción especial
        return "__AI_QUERY_WITH_REFORMULATE__"
    
    elif choice_id == "unsatisfied_support":
        # Ofrece contacto de soporte humano
        return {
            "type": "text",
            "body": (
                "👨‍💼 *Contacta a nuestro equipo:*\n\n"
                "📧 Email: contacto@financial.cl\n"
                "📱 WhatsApp: +56 9 XXXX-XXXX\n"
                "⏰ Horario: Lunes a Viernes, 9:00-18:00\n\n"
                "_Te responderemos en menos de 24 horas._"
            ),
        }
    
    elif choice_id == "unsatisfied_continue_roadmap":
        # Vuelve al roadmap
        _record_activity_safely(phone)
        from core.roadmaps import get_roadmap_text
        return get_roadmap_text(user)
    
    else:
        # Fallback (no debería ocurrir)
        return "No entendí tu elección. Escribe *'roadmap'* para ver tu progreso."