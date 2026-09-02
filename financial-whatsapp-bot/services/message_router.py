import logging

from db.users import get_user, reset_user_profile, save_user
from core.fund_flow import handle_fund_message, should_handle_fund_message
from core.menu import MENU_BUTTON, get_menu_widget
from core.roadmaps import (
    get_roadmap_text,
    mark_hito_done,
    revert_last_hito,
    get_pending_milestone,
    HITO_LISTO_ID,
    HITO_AYUDA_ID,
    HITO_VOLVER_ID,
    MENU_FINANCIAL_ID,
)
from core.onboarding import process_onboarding
from db.reminders import (
    clear_completed_roadmap_schedule_by_phone,
    disable_reminders,
    enable_reminders,
    record_incoming_reminder_reply,
    record_roadmap_activity,
)

logger = logging.getLogger("financial")

# Textos exactos que envía Meta cuando el usuario presiona
# los botones de la plantilla tributaria (HdU07).
VER_MAS_INFO_TRIGGERS = [
    "ver más información.",
    "ver mas informacion.",
    "ver más información",
    "ver mas informacion",
]
 
YA_LO_REALICE_TRIGGERS = [
    "ya lo realicé.",
    "ya lo realice.",
    "ya lo realicé",
    "ya lo realice",
]


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


def _clear_completed_roadmap_safely(phone: str) -> None:
    try:
        clear_completed_roadmap_schedule_by_phone(phone)
    except Exception as error:
        logger.error(
            "No se pudieron limpiar los recordatorios del roadmap completo: %s",
            error,
        )


def route_message(
    phone: str,
    message: str,
    reply_to_message_id: str | None = None,
):
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
        new_user = reset_user_profile(phone, user)
        if not new_user:
            return (
                "No pude reiniciar tu perfil en este momento. "
                "Inténtalo nuevamente más tarde."
            )
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
            return {
                "type": "buttons",
                "body": (
                    "No pude activar los recordatorios en este momento. "
                    "Inténtalo nuevamente más tarde."
                ),
                "options": MENU_BUTTON,
            }
        return {
            "type": "buttons",
            "body": (
                "🔔 *Recordatorios activados.*\n\n"
                "Te avisaremos ante alertas importantes para tu negocio. Puedes pausarlos cuando quieras "
                "escribiendo *\"pausar recordatorios\"* o desde el menú."
            ),
            "options": MENU_BUTTON,
        }

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
            return {
                "type": "buttons",
                "body": (
                    "No pude pausar los recordatorios en este momento. "
                    "Inténtalo nuevamente más tarde."
                ),
                "options": MENU_BUTTON,
            }
        return {
            "type": "buttons",
            "body": (
                "🔕 *Recordatorios pausados.*\n\n"
                "Puedes volver a activarlos cuando quieras escribiendo "
                "*\"activar recordatorios\"* o desde el menú."
            ),
            "options": MENU_BUTTON,
        }

    replied_to_reminder = False
    if reply_to_message_id or int(user.get("reminder_count") or 0) > 0:
        replied_to_reminder = _record_reply_safely(
            phone,
            reply_to_message_id,
        )

    # ── Fund application flow ──
    # Se procesa antes del roadmap y de la IA para que respuestas breves como
    # "sí", "no" o una cifra se asocien a la pregunta pendiente del fondo.
    if should_handle_fund_message(user, message):
        try:
            return handle_fund_message(user, message)
        except Exception as error:
            logger.exception("No se pudo procesar el flujo de fondos: %s", error)
            return (
                "No pude procesar la evaluación de fondos en este momento. "
                "Inténtalo nuevamente más tarde."
            )

    # ── Roadmap / Plan de crecimiento ──
    roadmap_triggers = [
        "roadmap", "mi roadmap", "hitos", "qué me falta", "que me falta",
        "formalizar", "mis pasos", "mi ruta", "plan de crecimiento",
        "mi plan de crecimiento", "menu_roadmap"
    ]
    if any(trigger in msg_lower for trigger in roadmap_triggers):
        _record_activity_safely(phone)
        return get_roadmap_text(user)

    # ── Mark hito done ──
    done_triggers = [
        "listo", "hecho", "completado", "ya lo hice", "ya está",
        "ya esta", "siguiente", "menu_listo", HITO_LISTO_ID
    ]
    if any(trigger in msg_lower for trigger in done_triggers):
        response = mark_hito_done(user, save_user)
        if get_pending_milestone(user) is None:
            _clear_completed_roadmap_safely(phone)
        else:
            _record_activity_safely(phone)
        return response

    # ── Revert last hito (deshacer paso) ──
    if msg_lower in [HITO_VOLVER_ID, "deshacer", "deshacer paso"]:
        response = revert_last_hito(user, save_user)
        _record_activity_safely(phone)
        return response

    # ── Ayuda contextual del hito ──
    if msg_lower == HITO_AYUDA_ID:
        pending_hito = get_pending_milestone(user)
        if pending_hito:
            return "__AI_QUERY_WITH_CONTEXT__"
        else:
            return get_menu_widget(user)

    # ── Menu FinancIAl ──
    menu_triggers = [
        "ayuda", "help", "menu", "menú", "opciones",
        "menu_financial", MENU_FINANCIAL_ID
    ]
    if any(trigger == msg_lower for trigger in menu_triggers):
        return get_menu_widget(user)
    
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
            return response

    # ── Botón "Ver más información" de alerta tributaria (HdU07) ─────────────
    if msg_lower in VER_MAS_INFO_TRIGGERS:
        return (
            "📋 *Cómo declarar el F29 (IVA mensual)*\n\n"
            "1️⃣ Entra a *sii.cl* con tu RUT y clave\n"
            "2️⃣ Ve a *Servicios online → IVA → Declarar y pagar F29*\n"
            "3️⃣ Revisa los montos precargados y confirma\n"
            "4️⃣ Si no tuviste ventas ese mes, igual debes declarar con monto *0*\n\n"
            "💡 Si tienes dudas del proceso, escríbeme y te ayudo paso a paso.\n"
            "🔗 https://homer.sii.cl/"
        )
 
    # ── Botón "Ya lo realicé" de alerta tributaria (HdU07) ───────────────────
    if msg_lower in YA_LO_REALICE_TRIGGERS:
        return (
            "✅ *¡Excelente!* Gracias por confirmar que realizaste el trámite.\n\n"
            "Recuerda que el próximo F29 vence el *día 12 del mes siguiente*.\n\n"
            "¿Necesitas ayuda con otro trámite? Escríbeme cuando quieras. 💪"
        )

    # ── Respuesta a un recordatorio sin botón/comando reconocido ──
    # Se evalúa último, después de todos los botones e intenciones explícitas
    # (menú, insatisfacción, alertas), para que un botón concreto como
    # "menu_financial" siempre gane sobre este fallback genérico.
    if replied_to_reminder:
        _record_activity_safely(phone)
        return get_roadmap_text(user)

    # ── AI Chat (default) ──
    return "__AI_QUERY__"


def split_message(text: str, max_len: int) -> list[str]:
    if len(text) <= max_len:
        return [text]

    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break

        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len

        parts.append(text[:split_at])
        text = text[split_at:].lstrip("\n")

    return parts


UNSATISFIED_PATTERNS = {
    "no me sirvió", "no sirvio", "eso no me sirvió", "eso no sirvio",
    "no me funcionó", "no funciono", "sigo sin entender", "no entiendo",
    "aún tengo dudas", "todavia tengo dudas", "me sigue confundiendo",
    "confundido", "confundida", "eso no fue lo que", "no es lo que",
    "no era lo que", "no me sirve", "no me ayuda", "puedes explicar mejor",
    "explica mejor", "más detalles", "mas detalles",
}


def detect_unsatisfaction(message: str) -> bool:
    if not message:
        return False
    msg_lower = message.lower().strip()
    return any(pattern in msg_lower for pattern in UNSATISFIED_PATTERNS)


def handle_unsatisfaction_response(user: dict) -> dict:
    body = (
        "Entiendo que no quedó claro. 😊 *¿Qué prefieres hacer?*\n\n"
        "Puedo intentar explicarlo de otra forma, "
        "conectarte con un asesor real, o volvemos a tu panel."
    )
    return {
        "type": "buttons",
        "body": body,
        "options": [
            ("unsatisfied_reformulate", "🔄 Reformular respuesta"),
            ("unsatisfied_support", "👨‍💼 Hablar con asesor"),
            ("unsatisfied_continue_roadmap", "📋 Continuar"),
        ],
    }


def handle_unsatisfaction_choice(
    phone: str,
    choice_id: str,
    message: str,
    user: dict,
    save_user_fn,
) -> dict | str:
    if choice_id == "unsatisfied_reformulate":
        return "__AI_QUERY_WITH_REFORMULATE__"
    
    elif choice_id == "unsatisfied_support":
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
        _record_activity_safely(phone)
        return get_roadmap_text(user)
    
    return "No entendí tu elección. Escribe *'menu'* para ver tus opciones."
