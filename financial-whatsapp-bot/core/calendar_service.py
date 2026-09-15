"""Reglas compartidas del calendario personalizado.

Este módulo no consulta Supabase ni genera respuestas de WhatsApp. Valida,
convierte y formatea fechas para que el panel web y el flujo conversacional
puedan utilizar el mismo comportamiento.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import REMINDER_TIMEZONE


logger = logging.getLogger("financial")

ALLOWED_REMINDER_DAYS = {0, 1, 3, 7}


@lru_cache(maxsize=1)
def get_calendar_timezone() -> ZoneInfo:
    """Obtiene la zona horaria configurada para el calendario."""
    try:
        return ZoneInfo(REMINDER_TIMEZONE)
    except ZoneInfoNotFoundError as error:
        logger.exception(
            "No se encontró la zona horaria configurada: %s",
            REMINDER_TIMEZONE,
        )
        raise RuntimeError(
            f"No se encontró la zona horaria {REMINDER_TIMEZONE}"
        ) from error


def _ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("La fecha no tiene una zona horaria definida")
    return value.astimezone(timezone.utc)


def normalize_local_event_at(
    event_at: datetime,
    *,
    now: datetime | None = None,
    require_future: bool = True,
) -> datetime:
    """Interpreta una fecha local de Chile y la devuelve en UTC."""
    if event_at.tzinfo is None:
        local_timezone = get_calendar_timezone()
        local_event_at = event_at.replace(tzinfo=local_timezone)

        round_trip = (
            local_event_at
            .astimezone(timezone.utc)
            .astimezone(local_timezone)
            .replace(tzinfo=None)
        )
        if round_trip != event_at:
            raise ValueError(
                "La hora seleccionada no existe debido al cambio "
                "de horario en Chile"
            )

        event_at_utc = local_event_at.astimezone(timezone.utc)
    else:
        event_at_utc = event_at.astimezone(timezone.utc)

    current = now or datetime.now(timezone.utc)
    current_utc = _ensure_aware_utc(current)
    if require_future and event_at_utc <= current_utc:
        raise ValueError("La fecha del evento debe estar en el futuro")

    return event_at_utc


def calculate_reminder_at(
    event_at: datetime,
    reminder_days_before: int,
    *,
    now: datetime | None = None,
) -> datetime:
    """Calcula el recordatorio en horario chileno y devuelve UTC."""
    if reminder_days_before not in ALLOWED_REMINDER_DAYS:
        allowed = ", ".join(str(value) for value in sorted(ALLOWED_REMINDER_DAYS))
        raise ValueError(
            f"La anticipación debe ser uno de estos valores: {allowed}"
        )

    event_at_utc = _ensure_aware_utc(event_at)
    local_event_at = event_at_utc.astimezone(get_calendar_timezone())
    local_reminder_at = local_event_at - timedelta(days=reminder_days_before)
    reminder_at_utc = local_reminder_at.astimezone(timezone.utc)

    current = now or datetime.now(timezone.utc)
    current_utc = _ensure_aware_utc(current)
    if reminder_at_utc < current_utc:
        reminder_at_utc = current_utc

    if reminder_at_utc > event_at_utc:
        raise ValueError(
            "El recordatorio no puede programarse después del evento"
        )

    return reminder_at_utc


def prepare_event_schedule(
    event_at: datetime,
    reminder_days_before: int,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Prepara event_at y reminder_at para guardarlos en Supabase."""
    current = now or datetime.now(timezone.utc)
    event_at_utc = normalize_local_event_at(
        event_at,
        now=current,
        require_future=True,
    )
    reminder_at_utc = calculate_reminder_at(
        event_at_utc,
        reminder_days_before,
        now=current,
    )
    return event_at_utc, reminder_at_utc


def parse_stored_datetime(value: str | datetime) -> datetime:
    """Convierte una fecha recuperada desde Supabase a datetime UTC."""
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(
                "La fecha guardada tiene un formato inválido"
            ) from error

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_calendar_datetime(
    value: str | datetime,
    *,
    include_year: bool = True,
) -> str:
    """Formatea una fecha almacenada para mostrarla en horario chileno."""
    local_value = parse_stored_datetime(value).astimezone(
        get_calendar_timezone()
    )
    if include_year:
        return local_value.strftime("%d/%m/%Y a las %H:%M")
    return local_value.strftime("%d/%m a las %H:%M")
