import logging
from services.performance import observe_route, set_case

from config import FINANCIAL_MOVEMENTS_ENABLED, FORMALIZATION_DIAGNOSTICS_ENABLED
from core.formalization_flow import (
    PASS_TO_AI as DIAGNOSTIC_PASS_TO_AI,
    handle_diagnostic_message,
    is_diagnostic_entry,
)
from db.users import (
    UserPersistenceError,
    create_user_profile,
    get_user,
    reset_user_profile,
    save_user,
    update_onboarding_profile,
    update_user_fields,
)
from core.calendar_flow import (
    handle_calendar_message,
    is_calendar_entry_message,
    should_exit_calendar_message,
    should_handle_calendar_message,
)
from core.fund_flow import (
    FUND_ANSWER_PREFIX,
    handle_fund_message,
    is_fund_entry_message,
    should_handle_fund_message,
)
from core.financial_flow import (
    handle_financial_message,
    is_financial_entry_message,
    should_exit_financial_message,
    should_handle_financial_message,
)
from core.expert_flow import handle_expert_message
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
from core.onboarding import (
    PROFILE_BACK_ID,
    PROFILE_SKIP_ADDRESS_ID,
    needs_onboarding,
    process_onboarding,
)
from db.calendar import clear_calendar_session, get_calendar_session
from db.message_context import NOT_LOADED, MessageContext
from db.fondos import (
    cancel_fund_session,
    get_active_fund_session,
    get_requirement_definitions,
    normalize_fund_text,
)
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
PAUSE_REMINDERS_COMMANDS = {
    "pausar recordatorios", "desactivar recordatorios", "no quiero recordatorios",
    "menu_recordatorios_off",
}

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


def _is_explicit_other_module_action(message: str) -> bool:
    """Permite navegar sin confundir preguntas con acciones sobre el roadmap."""
    raw = message.strip().lower()
    if raw.startswith(("menu_", "calendar_", "finance_", "hito_", "unsatisfied_", "diag:", "fund_")):
        return True
    if is_diagnostic_entry(message) or is_fund_entry_message(message):
        return True
    if is_calendar_entry_message(message):
        return True
    if FINANCIAL_MOVEMENTS_ENABLED and is_financial_entry_message(message):
        return True
    if "?" in message or "¿" in message:
        return False
    commands = {
        *RESET_COMMANDS, *MENU_PHRASES,
        "ayuda", "help", "menu", "menú", "opciones",
        "roadmap", "mi roadmap", "hitos", "qué me falta", "que me falta",
        "formalizar", "mis pasos", "mi ruta", "plan de crecimiento",
        "mi plan de crecimiento", "listo", "hecho", "completado",
        "ya lo hice", "ya está", "ya esta", "siguiente", "deshacer", "deshacer paso",
        "activar recordatorios", "acepto recordatorios", "reanudar recordatorios",
        "pausar recordatorios", "desactivar recordatorios", "no quiero recordatorios",
        *VER_MAS_INFO_TRIGGERS, *YA_LO_REALICE_TRIGGERS,
    }
    normalized = normalize_fund_text(message)
    return normalized in {normalize_fund_text(command) for command in commands}


def _record_reply_safely(
    phone: str, reply_to_message_id: str | None, user: dict | None = None,
) -> bool:
    try:
        user_id = (user or {}).get("id")
        if user_id:
            return record_incoming_reminder_reply(phone, reply_to_message_id, user_id=user_id)
        return record_incoming_reminder_reply(phone, reply_to_message_id)
    except Exception as error:
        logger.error("No se pudo registrar la respuesta al recordatorio: %s", error)
        return False


def _record_activity_safely(phone: str, user: dict | None = None) -> None:
    try:
        if user is not None and "roadmap" in user:
            record_roadmap_activity(phone, user=user)
        else:
            record_roadmap_activity(phone)
    except Exception as error:
        logger.error("No se pudo registrar la actividad del roadmap: %s", error)


# Columnas que modifican mark_hito_done y revert_last_hito (core/roadmaps.py).
_ROADMAP_FIELDS = ("roadmap", "inicio_sii", "roadmap_completed_at", "roadmap_completion_stats")


def _save_roadmap_fields(phone: str, user: dict) -> dict | None:
    """Guarda solo el avance del roadmap, sin reescribir el perfil completo."""
    return update_user_fields(phone, {key: user[key] for key in _ROADMAP_FIELDS if key in user})


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


@observe_route
def route_message(
    phone: str,
    message: str,
    reply_to_message_id: str | None = None,
    message_id: str | None = None,
    context: MessageContext | None = None,
):
    """Decide la respuesta de un mensaje.

    context (opcional) trae en una lectura el perfil y los estados de
    conversación (ver db/message_context.py). Sin él, cada estado se consulta
    por separado como antes; la lógica de decisión es la misma en ambos casos.
    """
    message = message.strip()
    msg_lower = message.lower()

    # Get or create user
    use_context = context is not None and not context.stale and context.user is not None
    try:
        user = context.user if use_context else get_user(phone, strict=True)
        if not user:
            user = create_user_profile(phone)
    except UserPersistenceError:
        return "No pude consultar o guardar tu perfil en este momento. Inténtalo nuevamente más tarde."
    preloaded_formalization = context.formalization if use_context else NOT_LOADED

    # Reiniciar y retirar los recordatorios siguen disponibles durante el registro.
    bypass_onboarding = (
        msg_lower in RESET_COMMANDS or msg_lower.rstrip(".") in PAUSE_REMINDERS_COMMANDS
    )

    # ── Onboarding flow ──
    if user.get("onboarding_step") != "done" and not bypass_onboarding:
        response = process_onboarding(user, message, update_onboarding_profile)
        if response:
            set_case("onboarding")
            return response

    # ── Contacto con un experto (HdU16) ──
    # Va antes del resto de los flujos: mientras un experto conversa con el
    # emprendedor, el asistente de IA queda en pausa para él.
    expert_response = handle_expert_message(
        user, message, RESET_COMMANDS, message_id,
        open_request=context.expert_request if use_context else NOT_LOADED,
    )
    if expert_response is not None:
        set_case("expert")
        if FORMALIZATION_DIAGNOSTICS_ENABLED:
            # El relé humano mantiene prioridad y no deja una pregunta numérica
            # del diagnóstico activa para cuando termine la conversación.
            handle_diagnostic_message(
                user, "menu_experto", external_action=True,
                preloaded_context=preloaded_formalization,
            )
        return expert_response

    # Los usuarios antiguos completan solo los datos faltantes. La conversación
    # con un experto mantiene prioridad y su roadmap nunca se reinicia aquí.
    if not bypass_onboarding and needs_onboarding(user):
        set_case("onboarding")
        return process_onboarding(user, message, update_onboarding_profile)
    if msg_lower in {PROFILE_BACK_ID, PROFILE_SKIP_ADDRESS_ID}:
        return get_menu_widget(user, prefix="✅ Tu perfil ya está completo. Ese botón corresponde a una pregunta anterior.\n\n")

    # El diagnóstico corre en el mismo threadpool del router, sin IA ni nuevas
    # tareas de Redis. Debe poder salir antes de los triggers del roadmap y de
    # las sesiones de otros módulos (también de sus botones hardcodeados).
    if FORMALIZATION_DIAGNOSTICS_ENABLED:
        # diag: es navegación para los otros módulos, pero una respuesta interna
        # para el diagnóstico. No pausar su sesión ni enviar ese ID a la IA.
        external_action = (
            not message.strip().startswith("diag:")
            and _is_explicit_other_module_action(message)
        )
        diagnostic_response = handle_diagnostic_message(
            user, message, external_action=external_action,
            message_id=message_id, preloaded_context=preloaded_formalization,
        )
        if diagnostic_response is not None:
            set_case("diagnostic")
            if is_diagnostic_entry(message):
                _clear_calendar_session_safely(user.get("id"))
                _clear_financial_session_safely(user.get("id"))
                try:
                    cancel_fund_session(user["id"])
                except Exception:
                    logger.exception("No se pudo cerrar fondos al abrir el diagnóstico")
            return "__AI_QUERY__" if diagnostic_response == DIAGNOSTIC_PASS_TO_AI else diagnostic_response

    # Leer una sola vez el contexto de fondos, también antes de los comandos
    # que retornan temprano (reiniciar/recordatorios). Cancelar la sesión no
    # elimina fund_user_answers: al retomar solo se preguntará lo pendiente.
    fund_session = None
    fund_definition = None
    try:
        if use_context:
            fund_session = context.fund_session
            fund_definition = context.fund_definition
        else:
            if user.get("id"):
                fund_session = get_active_fund_session(user["id"])
            if fund_session and fund_session.get("pending_field_key"):
                key = fund_session["pending_field_key"]
                fund_definition = get_requirement_definitions([key]).get(key)
        handles_fund = should_handle_fund_message(
            user, message, fund_session, fund_definition,
        )
        if msg_lower.startswith(FUND_ANSWER_PREFIX) and (
            not fund_session or not fund_session.get("pending_field_key")
        ):
            # Un botón antiguo no debe cerrar el borrador de otro módulo.
            return handle_fund_message(user, message, fund_session, fund_definition)
        if fund_session and not handles_fund:
            cancel_fund_session(user["id"])
            fund_session = None
            if not _is_explicit_other_module_action(message):
                if reply_to_message_id or int(user.get("reminder_count") or 0) > 0:
                    _record_reply_safely(phone, reply_to_message_id, user)
                # Una consulta como "¿qué me falta para formalizar?" no debe
                # activar los comandos por substring que aparecen más abajo.
                return "__AI_QUERY__"
    except Exception as error:
        logger.exception("No se pudo consultar o cerrar la sesión de fondos: %s", error)
        return (
            "No pude procesar la evaluación de fondos en este momento. "
            "Inténtalo nuevamente más tarde."
        )

    # Las entradas y botones de fondos también deben poder salir de otro
    # borrador, sin quedar capturados por el calendario o las finanzas.
    # Sesiones ya leídas en el contexto; se descartan si este mensaje las borra.
    prefetched_financial = context.financial_session if use_context else NOT_LOADED
    prefetched_calendar = context.calendar_session if use_context else NOT_LOADED

    if handles_fund and (
        is_fund_entry_message(message) or msg_lower.startswith("fund_")
    ):
        _clear_calendar_session_safely(user.get("id"))
        _clear_financial_session_safely(user.get("id"))
        if use_context:
            prefetched_financial = prefetched_calendar = None

    # ── Reset command ──
    if msg_lower in RESET_COMMANDS:
        set_case("onboarding")
        _clear_calendar_session_safely(user.get("id"))
        _clear_financial_session_safely(user.get("id"))
        new_user = reset_user_profile(phone, user)
        if not new_user:
            return (
                "No pude reiniciar tu perfil en este momento. "
                "Inténtalo nuevamente más tarde."
            )
        return process_onboarding(new_user, message, update_onboarding_profile)

    if msg_lower == "menu_recordatorios":
        set_case("reminders")
        return {"type": "buttons", "body": "¿Qué quieres hacer con tus recordatorios?",
                "options": [("menu_recordatorios_on", "Activar"), ("menu_recordatorios_off", "Pausar"), *MENU_BUTTON]}

    activation_commands = {
        "activar recordatorios",
        "acepto recordatorios",
        "reanudar recordatorios",
        "menu_recordatorios_on",
    }
    if msg_lower in activation_commands:
        set_case("reminders")
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

    if msg_lower.rstrip(".") in PAUSE_REMINDERS_COMMANDS:
        set_case("reminders")
        _record_reply_safely(phone, reply_to_message_id, user)
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
            user,
        )

    # ── Ingresos y gastos en lenguaje natural (HdU13) ──
    # Se evalúa antes de calendario/fondos para que un movimiento explícito
    # pueda cambiar de módulo. Los botones usan el namespace finance_* y una
    # sesión solo captura las respuestas que realmente le corresponden.
    if FINANCIAL_MOVEMENTS_ENABLED:
        financial_session = None
        financial_entry = is_financial_entry_message(message)
        try:
            if prefetched_financial is not NOT_LOADED:
                financial_session = prefetched_financial
            elif user.get("id"):
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
            set_case("finances")
            try:
                if financial_entry:
                    _clear_calendar_session_safely(user.get("id"))
                    try:
                        if fund_session is not None:
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
        if prefetched_calendar is not NOT_LOADED:
            calendar_session = prefetched_calendar
        elif user.get("id"):
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
        set_case("calendar")
        try:
            if calendar_entry:
                try:
                    if fund_session is not None:
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
    if handles_fund:
        set_case("funds")
        try:
            return handle_fund_message(user, message, fund_session, fund_definition)
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
    asks_personal_data = any(
        term in msg_lower
        for term in (
            "cuanto", "cuánto", "costo", "cuesta", "meta", "me falta",
            "me faltan", "postul",
        )
    )
    if any(trigger in msg_lower for trigger in roadmap_triggers) and not asks_personal_data:
        set_case("roadmap")
        _record_activity_safely(phone, user)
        return get_roadmap_text(user)

    # ── Mark hito done ──
    done_triggers = [
        "listo", "hecho", "completado", "ya lo hice", "ya está",
        "ya esta", "siguiente", "menu_listo", HITO_LISTO_ID
    ]
    if any(trigger in msg_lower for trigger in done_triggers):
        set_case("roadmap")
        response = mark_hito_done(user, _save_roadmap_fields)
        if get_pending_milestone(user) is None:
            _clear_completed_roadmap_safely(phone)
        else:
            _record_activity_safely(phone, user)
        return response

    # ── Revert last hito (deshacer paso) ──
    if msg_lower in [HITO_VOLVER_ID, "deshacer", "deshacer paso"]:
        set_case("roadmap")
        response = revert_last_hito(user, _save_roadmap_fields)
        _record_activity_safely(phone, user)
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
        set_case("panel")
        return "__WEB_PANEL_LINK__"

    # ── Menu FinancIAl ──
    menu_triggers = [
        "ayuda", "help", "menu", "menú", "opciones",
        "menu_financial", MENU_FINANCIAL_ID
    ]
    if any(trigger == msg_lower for trigger in menu_triggers) or _mentions_menu(msg_lower):
        set_case("menu")
        return get_menu_widget(user)
    
    # ── Manejo de insatisfacción ──
    if detect_unsatisfaction(message):
        record_unsatisfaction(user, message)
        response = handle_unsatisfaction_response(user)
        _record_reply_safely(phone, reply_to_message_id, user)
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
        set_case("tax_info")
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
        set_case("tax_info")
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
        set_case("roadmap")
        _record_activity_safely(phone, user)
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
    "no me sirvió", "no sirvio", "no sirvió", "eso no me sirvió", "eso no sirvio",
    "no me funcionó", "no funciono", "sigo sin entender", "no entiendo",
    "aún tengo dudas", "todavia tengo dudas", "me sigue confundiendo",
    "confundido", "confundida", "eso no fue lo que", "no es lo que",
    "no era lo que", "no me sirve", "no me ayuda", "puedes explicar mejor",
    "explica mejor", "más detalles", "mas detalles", "no entendí", "no entendi", "no me quedó claro", "no me quedo claro",
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
        _record_activity_safely(phone, user)
        return get_roadmap_text(user)
    
    return "No entendí tu elección. Escribe *'menu'* para ver tus opciones."
