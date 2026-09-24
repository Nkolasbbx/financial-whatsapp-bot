import logging

from config import FINANCIAL_MOVEMENTS_ENABLED
from db.users import get_user, reset_user_profile, save_user
from core.calendar_flow import (
    handle_calendar_message,
    is_calendar_entry_message,
    should_exit_calendar_message,
    should_handle_calendar_message,
)
from core.fund_flow import handle_fund_message, should_handle_fund_message
from core.financial_flow import (
    handle_financial_message,
    is_financial_entry_message,
    should_exit_financial_message,
    should_handle_financial_message,
)
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
from db.calendar import clear_calendar_session, get_calendar_session
from db.fondos import cancel_fund_session
from db.financial_movements import (
    clear_financial_session,
    get_financial_session,
)
from db.reminders import (
    clear_completed_roadmap_schedule_by_phone,
    disable_reminders,
    enable_reminders,
    record_incoming_reminder_reply,
    record_roadmap_activity,
)
from db.assistant_feedback import record_unsatisfaction

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

# Comandos que reinician el perfil del usuario — se reutiliza este set en
# routers/webhook.py para pedir confirmación cuando el comando llega
# transcrito desde un audio (HdU11, AC3).
RESET_COMMANDS = {"reiniciar", "reset", "empezar de nuevo", "menu_reiniciar"}

# Frases de navegación al menú del bot. A propósito son frases completas y
# no la palabra suelta "menú": muchos usuarios son emprendedores de comida
# y "menú" en su mensaje suele referirse al menú de SU negocio (ej. "¿qué
# necesito para mi menú de comida?"), no al menú del bot — esa pregunta
# tiene que seguir cayendo a la IA, no interceptarse acá.
MENU_PHRASES = (
    "menu principal", "menú principal",
    "ver el menu", "ver el menú", "ver menu", "ver menú",
    "volver al menu", "volver al menú",
    "mostrar el menu", "mostrar el menú", "mostrar menu", "mostrar menú",
    "quiero el menu", "quiero el menú",
    "quiero ver el menu", "quiero ver el menú",
)


def _mentions_menu(msg_lower: str) -> bool:
    return any(phrase in msg_lower for phrase in MENU_PHRASES)


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


def _clear_calendar_session_safely(user_id: str | None) -> None:
    if not user_id:
        return
    try:
        clear_calendar_session(user_id)
    except Exception as error:
        logger.error("No se pudo limpiar la sesión del calendario: %s", error)


def _clear_financial_session_safely(user_id: str | None) -> None:
    if not user_id:
        return
    try:
        clear_financial_session(user_id)
    except Exception as error:
        logger.error("No se pudo limpiar la sesión financiera: %s", error)


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
    if msg_lower in RESET_COMMANDS:
        _clear_calendar_session_safely(user.get("id"))
        _clear_financial_session_safely(user.get("id"))
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

    # ── Ingresos y gastos en lenguaje natural (HdU13) ──
    # Se evalúa antes de calendario/fondos para que un movimiento explícito
    # pueda cambiar de módulo. Los botones usan el namespace finance_* y una
    # sesión solo captura las respuestas que realmente le corresponden.
    if FINANCIAL_MOVEMENTS_ENABLED:
        financial_session = None
        financial_entry = is_financial_entry_message(message)
        try:
            if user.get("id"):
                financial_session = get_financial_session(user["id"])
        except Exception as error:
            logger.error("No se pudo consultar la sesión financiera: %s", error)
            if financial_entry:
                return {
                    "type": "buttons",
                    "body": (
                        "No pude abrir tus movimientos en este momento. "
                        "Inténtalo nuevamente más tarde."
                    ),
                    "options": MENU_BUTTON,
                }

        if financial_session and should_exit_financial_message(message):
            _clear_financial_session_safely(user.get("id"))
            financial_session = None

        if should_handle_financial_message(message, financial_session):
            try:
                if financial_entry:
                    _clear_calendar_session_safely(user.get("id"))
                    try:
                        cancel_fund_session(user["id"])
                    except Exception as error:
                        logger.error(
                            "No se pudo cerrar la sesión de fondos al abrir "
                            "movimientos: %s",
                            error,
                        )
                return handle_financial_message(
                    user,
                    message,
                    financial_session,
                )
            except Exception as error:
                logger.exception(
                    "No se pudo procesar el flujo financiero: %s",
                    error,
                )
                return {
                    "type": "buttons",
                    "body": (
                        "No pude procesar tus movimientos en este momento. "
                        "Inténtalo nuevamente más tarde."
                    ),
                    "options": MENU_BUTTON,
                }

    # ── Calendario personalizado (HdU08) ──
    # La sesión se persiste en Supabase, por lo que el flujo sobrevive a un
    # reinicio del servidor y funciona igual en local y en Railway.
    calendar_session = None
    calendar_entry = is_calendar_entry_message(message)
    try:
        if user.get("id"):
            calendar_session = get_calendar_session(user["id"])
    except Exception as error:
        logger.error("No se pudo consultar la sesión del calendario: %s", error)
        if calendar_entry:
            return {
                "type": "buttons",
                "body": (
                    "No pude abrir tu calendario en este momento. "
                    "Inténtalo nuevamente más tarde."
                ),
                "options": MENU_BUTTON,
            }

    if calendar_session and should_exit_calendar_message(message):
        _clear_calendar_session_safely(user.get("id"))
        calendar_session = None

    if should_handle_calendar_message(message, calendar_session):
        try:
            if calendar_entry:
                try:
                    cancel_fund_session(user["id"])
                except Exception as error:
                    logger.error(
                        "No se pudo cerrar la sesión de fondos al abrir el calendario: %s",
                        error,
                    )
            return handle_calendar_message(user, message, calendar_session)
        except Exception as error:
            logger.exception("No se pudo procesar el calendario personalizado: %s", error)
            return {
                "type": "buttons",
                "body": (
                    "No pude procesar tu calendario en este momento. "
                    "Inténtalo nuevamente más tarde."
                ),
                "options": MENU_BUTTON,
            }

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

    # ── Panel web (login sin contraseña vía link de un solo uso) ──
    if msg_lower == "menu_panel_web":
        return "__WEB_PANEL_LINK__"

    # ── Menu FinancIAl ──
    menu_triggers = [
        "ayuda", "help", "menu", "menú", "opciones",
        "menu_financial", MENU_FINANCIAL_ID
    ]
    if any(trigger == msg_lower for trigger in menu_triggers) or _mentions_menu(msg_lower):
        return get_menu_widget(user)
    
    # ── Manejo de insatisfacción ──
    if detect_unsatisfaction(message):
        record_unsatisfaction(user, message)
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
    "explica mejor", "más detalles", "mas detalles", "no entendí", "no entendí", "no me quedó claro", "no me quedo claro",
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
