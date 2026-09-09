from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config import CALENDAR_DEFAULT_HOUR, REMINDER_TIMEZONE
from core.menu import MENU_BUTTON
from db.calendar import (
    cancel_calendar_event,
    clear_calendar_session,
    create_calendar_event,
    get_active_calendar_events,
    get_calendar_event,
    get_calendar_session,
    start_calendar_session,
    update_calendar_event_date,
    update_calendar_session,
)

logger = logging.getLogger("financial")

CALENDAR_MENU_ID = "menu_calendar"
CALENDAR_CREATE_ID = "calendar_create"
CALENDAR_VIEW_ID = "calendar_view"
CALENDAR_CANCEL_ID = "calendar_cancel"
CALENDAR_RESTART_ID = "calendar_restart"
CALENDAR_CONFIRM_CREATE_ID = "calendar_confirm_create"
CALENDAR_CONFIRM_UPDATE_ID = "calendar_confirm_update"
CALENDAR_CONFIRM_DELETE_ID = "calendar_confirm_delete"

CALENDAR_EVENT_PREFIX = "calendar_event:"
CALENDAR_CHANGE_PREFIX = "calendar_change:"
CALENDAR_DELETE_PREFIX = "calendar_delete:"
CALENDAR_PAGE_PREFIX = "calendar_page:"

CALENDAR_PAGE_SIZE = 8

_MENU_COMMANDS = {"calendario", "menu calendario"}
_CREATE_COMMANDS = {
    "crear fecha",
    "crear fecha importante",
    "agendar fecha",
    "agendar compromiso",
    "crear recordatorio",
    "nuevo evento",
}
_VIEW_COMMANDS = {
    "mi calendario",
    "ver mi calendario",
    "mis fechas",
    "ver mis fechas",
    "mis eventos",
    "cambiar fecha",
    "eliminar fecha",
}
_CANCEL_COMMANDS = {"cancelar", "salir", "cancelar calendario"}
_EXTERNAL_COMMANDS = {
    "menu",
    "menu principal",
    "ayuda",
    "roadmap",
    "mi roadmap",
    "postular fondos",
    "postular a fondos",
    "activar recordatorios",
    "pausar recordatorios",
    "reiniciar",
    "reset",
}


def normalize_calendar_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").strip().lower())
    return " ".join(
        "".join(character for character in normalized if not unicodedata.combining(character))
        .replace("_", " ")
        .split()
    )


def _local_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(REMINDER_TIMEZONE)
    except Exception as error:
        logger.error(
            "Zona horaria %s no disponible; se usará UTC: %s",
            REMINDER_TIMEZONE,
            error,
        )
        return ZoneInfo("UTC")


def _parse_event_at(value: str, now: datetime | None = None) -> datetime:
    """Convierte una fecha chilena a UTC.

    Acepta DD/MM/AAAA o DD-MM-AAAA y, opcionalmente, HH:MM. Cuando no se
    especifica hora se usa CALENDAR_DEFAULT_HOUR en la zona configurada.
    """
    raw = (value or "").strip()
    match = re.fullmatch(
        r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?:\s+(\d{1,2}):(\d{2}))?",
        raw,
    )
    if not match:
        raise ValueError("Formato de fecha inválido")

    day, month, year = (int(match.group(index)) for index in (1, 2, 3))
    hour = int(match.group(4)) if match.group(4) is not None else CALENDAR_DEFAULT_HOUR
    minute = int(match.group(5)) if match.group(5) is not None else 0
    if hour > 23 or minute > 59:
        raise ValueError("Hora inválida")

    local_tz = _local_timezone()
    try:
        local_value = datetime(year, month, day, hour, minute, tzinfo=local_tz)
    except ValueError as error:
        raise ValueError("La fecha no existe") from error

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if local_value.astimezone(timezone.utc) <= current.astimezone(timezone.utc):
        raise ValueError("La fecha debe estar en el futuro")

    return local_value.astimezone(timezone.utc)


def _parse_stored_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_event_at(value: str | datetime) -> str:
    local_value = _parse_stored_datetime(value).astimezone(_local_timezone())
    return local_value.strftime("%d/%m/%Y a las %H:%M")


def _short_event_label(event: dict) -> str:
    date_text = _parse_stored_datetime(event["event_at"]).astimezone(
        _local_timezone()
    ).strftime("%d/%m")
    description = " ".join((event.get("description") or "Evento").split())
    label = f"{date_text} · {description}"
    return label[:24]


def _description_preview(value: str, max_length: int = 64) -> str:
    description = " ".join((value or "Evento").split())
    if len(description) <= max_length:
        return description
    return f"{description[:max_length - 1].rstrip()}…"


def _calendar_menu(prefix: str = "") -> dict:
    return {
        "type": "list",
        "body": (
            f"{prefix}📅 *Mi calendario de negocio*\n\n"
            "Agenda trámites, compromisos y fechas importantes. Te avisaré "
            "por WhatsApp cuando llegue la fecha."
        ),
        "button_text": "Ver opciones",
        "options": [
            (CALENDAR_CREATE_ID, "➕ Crear fecha"),
            (CALENDAR_VIEW_ID, "📅 Ver calendario"),
            MENU_BUTTON[0],
        ],
    }


def _date_prompt(prefix: str = "") -> dict:
    return {
        "type": "buttons",
        "body": (
            f"{prefix}📆 Escribe la fecha del compromiso en formato "
            "*DD/MM/AAAA*.\n\n"
            "También puedes indicar la hora, por ejemplo: "
            "*20/09/2026 15:30*. Si no la escribes, usaré las "
            f"*{CALENDAR_DEFAULT_HOUR:02d}:00*."
        ),
        "options": [(CALENDAR_CANCEL_ID, "Cancelar")],
    }


def _description_prompt(event_at: datetime) -> dict:
    return {
        "type": "buttons",
        "body": (
            f"✅ Fecha: *{_format_event_at(event_at)}*\n\n"
            "Ahora escribe una descripción breve.\n"
            "Ejemplo: *Renovar patente municipal*."
        ),
        "options": [
            (CALENDAR_RESTART_ID, "Cambiar fecha"),
            (CALENDAR_CANCEL_ID, "Cancelar"),
        ],
    }


def _creation_confirmation(event_at: str | datetime, description: str) -> dict:
    return {
        "type": "buttons",
        "body": (
            "📋 *Confirma tu nueva fecha*\n\n"
            f"📅 {_format_event_at(event_at)}\n"
            f"📝 {description}\n\n"
            "¿Quieres guardarla?"
        ),
        "options": [
            (CALENDAR_CONFIRM_CREATE_ID, "Guardar"),
            (CALENDAR_RESTART_ID, "Empezar de nuevo"),
            (CALENDAR_CANCEL_ID, "Cancelar"),
        ],
    }


def _update_confirmation(event: dict, event_at: str | datetime) -> dict:
    return {
        "type": "buttons",
        "body": (
            "📋 *Confirma el cambio de fecha*\n\n"
            f"📝 {event.get('description') or 'Evento'}\n"
            f"📅 Nueva fecha: {_format_event_at(event_at)}"
        ),
        "options": [
            (CALENDAR_CONFIRM_UPDATE_ID, "Cambiar fecha"),
            (CALENDAR_CANCEL_ID, "Cancelar"),
        ],
    }


def _delete_confirmation(event: dict) -> dict:
    return {
        "type": "buttons",
        "body": (
            "🗑️ *¿Eliminar esta fecha?*\n\n"
            f"📅 {_format_event_at(event['event_at'])}\n"
            f"📝 {event.get('description') or 'Evento'}"
        ),
        "options": [
            (CALENDAR_CONFIRM_DELETE_ID, "Sí, eliminar"),
            (CALENDAR_CANCEL_ID, "No, cancelar"),
        ],
    }


def _event_detail(event: dict) -> dict:
    event_id = event["id"]
    return {
        "type": "buttons",
        "body": (
            "📅 *Fecha guardada*\n\n"
            f"🗓️ {_format_event_at(event['event_at'])}\n"
            f"📝 {event.get('description') or 'Evento'}"
        ),
        "options": [
            (f"{CALENDAR_CHANGE_PREFIX}{event_id}", "Cambiar fecha"),
            (f"{CALENDAR_DELETE_PREFIX}{event_id}", "Eliminar"),
            (CALENDAR_VIEW_ID, "Ver calendario"),
        ],
    }


def _calendar_list(user_id: str, page: int = 0, prefix: str = "") -> dict:
    page = max(page, 0)
    offset = page * CALENDAR_PAGE_SIZE
    events = get_active_calendar_events(
        user_id,
        limit=CALENDAR_PAGE_SIZE + 1,
        offset=offset,
    )
    visible_events = events[:CALENDAR_PAGE_SIZE]
    has_next = len(events) > CALENDAR_PAGE_SIZE

    if not visible_events and page > 0:
        return _calendar_list(user_id, page - 1, prefix)
    if not visible_events:
        return _calendar_menu(
            f"{prefix}Todavía no tienes fechas futuras guardadas.\n\n"
        )

    options = [
        (f"{CALENDAR_EVENT_PREFIX}{event['id']}", _short_event_label(event))
        for event in visible_events
    ]
    if page > 0:
        options.append((f"{CALENDAR_PAGE_PREFIX}{page - 1}", "⬅️ Anteriores"))
    if has_next:
        options.append((f"{CALENDAR_PAGE_PREFIX}{page + 1}", "Siguientes ➡️"))

    lines = "\n".join(
        f"• *{_format_event_at(event['event_at'])}* — "
        f"{_description_preview(event.get('description') or '')}"
        for event in visible_events
    )
    return {
        "type": "list",
        "body": (
            f"{prefix}📅 *Tus próximas fechas*\n\n{lines}\n\n"
            "Selecciona una para cambiarla o eliminarla."
        ),
        "button_text": "Elegir fecha",
        "options": options,
    }


def is_calendar_entry_message(message: str) -> bool:
    raw = (message or "").strip().lower()
    normalized = normalize_calendar_text(message)
    return (
        raw in {CALENDAR_MENU_ID, CALENDAR_CREATE_ID, CALENDAR_VIEW_ID}
        or normalized in _MENU_COMMANDS
        or normalized in _CREATE_COMMANDS
        or normalized in _VIEW_COMMANDS
    )


def should_exit_calendar_message(message: str) -> bool:
    """Permite abandonar un borrador al elegir otra sección global."""
    raw = (message or "").strip().lower()
    normalized = normalize_calendar_text(message)
    return (
        raw.startswith("menu_") and raw != CALENDAR_MENU_ID
    ) or normalized in _EXTERNAL_COMMANDS


def should_handle_calendar_message(message: str, session: dict | None = None) -> bool:
    raw = (message or "").strip().lower()
    if is_calendar_entry_message(message):
        return True
    if raw in {
        CALENDAR_CANCEL_ID,
        CALENDAR_RESTART_ID,
        CALENDAR_CONFIRM_CREATE_ID,
        CALENDAR_CONFIRM_UPDATE_ID,
        CALENDAR_CONFIRM_DELETE_ID,
    }:
        return True
    if raw.startswith(
        (
            CALENDAR_EVENT_PREFIX,
            CALENDAR_CHANGE_PREFIX,
            CALENDAR_DELETE_PREFIX,
            CALENDAR_PAGE_PREFIX,
        )
    ):
        return True
    return session is not None


def handle_calendar_message(
    user: dict,
    message: str,
    session: dict | None = None,
) -> dict | str:
    user_id = user.get("id")
    if not user_id:
        return "No pude identificar tu perfil. Escribe *menu* e intenta nuevamente."

    raw = (message or "").strip().lower()
    normalized = normalize_calendar_text(message)

    if raw == CALENDAR_CANCEL_ID or normalized in _CANCEL_COMMANDS:
        clear_calendar_session(user_id)
        return _calendar_menu("Operación cancelada.\n\n")

    if raw == CALENDAR_RESTART_ID:
        start_calendar_session(user_id, "waiting_date")
        return _date_prompt()

    if raw == CALENDAR_MENU_ID or normalized in _MENU_COMMANDS:
        clear_calendar_session(user_id)
        return _calendar_menu()

    if raw == CALENDAR_CREATE_ID or normalized in _CREATE_COMMANDS:
        start_calendar_session(user_id, "waiting_date")
        return _date_prompt()

    if raw == CALENDAR_VIEW_ID or normalized in _VIEW_COMMANDS:
        clear_calendar_session(user_id)
        return _calendar_list(user_id)

    if raw.startswith(CALENDAR_PAGE_PREFIX):
        try:
            page = int(raw.removeprefix(CALENDAR_PAGE_PREFIX))
        except ValueError:
            page = 0
        return _calendar_list(user_id, page)

    if raw.startswith(CALENDAR_EVENT_PREFIX):
        event = get_calendar_event(user_id, raw.removeprefix(CALENDAR_EVENT_PREFIX))
        if not event or event.get("status") != "active":
            return _calendar_list(user_id, prefix="Esa fecha ya no está disponible.\n\n")
        clear_calendar_session(user_id)
        return _event_detail(event)

    if raw.startswith(CALENDAR_CHANGE_PREFIX):
        event_id = raw.removeprefix(CALENDAR_CHANGE_PREFIX)
        event = get_calendar_event(user_id, event_id)
        if not event or event.get("status") != "active":
            return _calendar_list(user_id, prefix="Esa fecha ya no está disponible.\n\n")
        start_calendar_session(user_id, "waiting_new_date", event_id=event_id)
        return _date_prompt(
            f"Cambiarás la fecha de *{event.get('description') or 'este evento'}*.\n\n"
        )

    if raw.startswith(CALENDAR_DELETE_PREFIX):
        event_id = raw.removeprefix(CALENDAR_DELETE_PREFIX)
        event = get_calendar_event(user_id, event_id)
        if not event or event.get("status") != "active":
            return _calendar_list(user_id, prefix="Esa fecha ya no está disponible.\n\n")
        start_calendar_session(user_id, "confirming_delete", event_id=event_id)
        return _delete_confirmation(event)

    session = session or get_calendar_session(user_id)
    if not session:
        return _calendar_menu()
    state = session.get("state")

    if raw == CALENDAR_CONFIRM_CREATE_ID and state == "confirming_creation":
        draft_event_at = session.get("draft_event_at")
        description = session.get("draft_description")
        if not draft_event_at or not description:
            start_calendar_session(user_id, "waiting_date")
            return _date_prompt("Faltaban datos para guardar la fecha. Intentemos otra vez.\n\n")
        event_at = _parse_stored_datetime(draft_event_at)
        created = create_calendar_event(user_id, description, event_at, event_at)
        clear_calendar_session(user_id)
        if not created:
            return "No pude guardar la fecha. Inténtalo nuevamente más tarde."
        reminder_note = (
            "Te enviaré el recordatorio por WhatsApp."
            if user.get("reminders_enabled")
            else "La fecha quedó guardada, pero debes activar los recordatorios para recibir el aviso."
        )
        return {
            "type": "buttons",
            "body": (
                "✅ *Fecha guardada*\n\n"
                f"📅 {_format_event_at(event_at)}\n"
                f"📝 {description}\n\n{reminder_note}"
            ),
            "options": [
                (CALENDAR_VIEW_ID, "Ver calendario"),
                (CALENDAR_CREATE_ID, "Crear otra fecha"),
                MENU_BUTTON[0],
            ],
        }

    if raw == CALENDAR_CONFIRM_UPDATE_ID and state == "confirming_update":
        event_id = session.get("event_id")
        draft_event_at = session.get("draft_event_at")
        if not event_id or not draft_event_at:
            clear_calendar_session(user_id)
            return _calendar_list(user_id, prefix="No pude recuperar el cambio pendiente.\n\n")
        event_at = _parse_stored_datetime(draft_event_at)
        updated = update_calendar_event_date(user_id, event_id, event_at, event_at)
        clear_calendar_session(user_id)
        if not updated:
            return _calendar_list(user_id, prefix="Esa fecha ya no está disponible.\n\n")
        return _calendar_list(user_id, prefix="✅ Fecha actualizada correctamente.\n\n")

    if raw == CALENDAR_CONFIRM_DELETE_ID and state == "confirming_delete":
        event_id = session.get("event_id")
        deleted = cancel_calendar_event(user_id, event_id) if event_id else None
        clear_calendar_session(user_id)
        if not deleted:
            return _calendar_list(user_id, prefix="Esa fecha ya no estaba disponible.\n\n")
        return _calendar_list(user_id, prefix="✅ Fecha eliminada de tu calendario.\n\n")

    if state in {"waiting_date", "waiting_new_date"}:
        try:
            event_at = _parse_event_at(message)
        except ValueError as error:
            return _date_prompt(f"No pude usar esa fecha: {error}.\n\n")

        if state == "waiting_new_date":
            event = get_calendar_event(user_id, session.get("event_id") or "")
            if not event or event.get("status") != "active":
                clear_calendar_session(user_id)
                return _calendar_list(user_id, prefix="Esa fecha ya no está disponible.\n\n")
            update_calendar_session(
                user_id,
                state="confirming_update",
                draft_event_at=event_at,
            )
            return _update_confirmation(event, event_at)

        update_calendar_session(
            user_id,
            state="waiting_description",
            draft_event_at=event_at,
        )
        return _description_prompt(event_at)

    if state == "waiting_description":
        description = (message or "").strip()
        if not description:
            return "Escribe una descripción breve para poder guardar la fecha."
        if len(description) > 500:
            return (
                "La descripción es demasiado larga. Resúmela en un máximo de "
                "500 caracteres."
            )
        draft_event_at = session.get("draft_event_at")
        if not draft_event_at:
            start_calendar_session(user_id, "waiting_date")
            return _date_prompt("No pude recuperar la fecha anterior. Intentemos otra vez.\n\n")
        update_calendar_session(
            user_id,
            state="confirming_creation",
            draft_description=description,
        )
        return _creation_confirmation(draft_event_at, description)

    if state == "confirming_creation":
        return _creation_confirmation(
            session.get("draft_event_at"),
            session.get("draft_description") or "Sin descripción",
        )
    if state == "confirming_update":
        event = get_calendar_event(user_id, session.get("event_id") or "")
        if event and session.get("draft_event_at"):
            return _update_confirmation(event, session["draft_event_at"])
    if state == "confirming_delete":
        event = get_calendar_event(user_id, session.get("event_id") or "")
        if event:
            return _delete_confirmation(event)

    clear_calendar_session(user_id)
    return _calendar_menu("El flujo anterior ya no estaba vigente.\n\n")
