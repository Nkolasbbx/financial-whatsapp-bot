"""Reglas de las convocatorias comunitarias (HdU019).

Este módulo no consulta Supabase ni envía mensajes: valida y normaliza los
datos que llegan desde el panel admin y arma los parámetros de la plantilla
de WhatsApp, para que el router, el servicio de envío y las pruebas usen
exactamente las mismas reglas.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from core.calendar_service import normalize_local_event_at
from services.calendar_reminders import format_calendar_template_date


CATEGORIES: tuple[tuple[str, str], ...] = (
    ("capacitacion", "Capacitación"),
    ("taller", "Taller"),
    ("feria", "Feria"),
    ("fondos", "Fondos y financiamiento"),
    ("charla", "Charla informativa"),
    ("networking", "Networking"),
    ("otra", "Otra"),
)
CATEGORY_LABELS = dict(CATEGORIES)

TITLE_MIN_CHARS = 3
TITLE_MAX_CHARS = 120
DESCRIPTION_MAX_CHARS = 1000
LOCATION_MAX_CHARS = 200
LINK_MAX_CHARS = 500

# Texto de respaldo para {{5}}: Meta rechaza plantillas con parámetros vacíos.
NOTICE_PLACE_FALLBACK = "Revisa los detalles en tu calendario web"

_DST_ERROR_FRAGMENT = "cambio de horario"
_URL_PATTERN = re.compile(r"^https?://\S+$", re.IGNORECASE)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _parse_local_datetime(
    value: Any,
    field_label: str,
    errors: list[str],
) -> datetime | None:
    """Convierte la fecha del formulario (hora de Chile) a UTC.

    Acepta el formato de <input type="datetime-local"> (AAAA-MM-DDTHH:MM) o
    un datetime. Los errores se agregan a la lista en vez de lanzarse, para
    informar todos los problemas del formulario de una sola vez.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        errors.append(f"Indica la fecha y hora {field_label}.")
        return None

    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"La fecha {field_label} no es válida.")
            return None

    try:
        return normalize_local_event_at(parsed, require_future=False)
    except ValueError as error:
        if _DST_ERROR_FRAGMENT in str(error):
            errors.append(
                f"La hora {field_label} no existe por el cambio de horario en Chile."
            )
        else:
            errors.append(f"La fecha {field_label} no es válida.")
        return None


def validate_community_event(
    data: dict[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Valida una convocatoria y devuelve (datos normalizados, errores).

    Si la lista de errores no está vacía, los datos no deben persistirse.
    Las fechas se devuelven como datetime en UTC.
    """
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    errors: list[str] = []

    title = " ".join(_clean_text(data.get("title")).split())
    if not title:
        errors.append("El título es obligatorio.")
    elif len(title) < TITLE_MIN_CHARS:
        errors.append(f"El título debe tener al menos {TITLE_MIN_CHARS} caracteres.")
    elif len(title) > TITLE_MAX_CHARS:
        errors.append(f"El título no puede superar los {TITLE_MAX_CHARS} caracteres.")

    category = _clean_text(data.get("category"))
    if not category:
        errors.append("Elige una categoría.")
    elif category not in CATEGORY_LABELS:
        errors.append("La categoría elegida no es válida.")

    description = _clean_text(data.get("description"))
    if not description:
        errors.append("La descripción es obligatoria.")
    elif len(description) > DESCRIPTION_MAX_CHARS:
        errors.append(
            f"La descripción no puede superar los {DESCRIPTION_MAX_CHARS} caracteres."
        )

    event_at = _parse_local_datetime(data.get("event_at"), "de la actividad", errors)
    notice_at = _parse_local_datetime(data.get("notice_at"), "del aviso", errors)

    if event_at is not None and event_at <= current:
        errors.append("La actividad debe ser en una fecha futura.")
    if notice_at is not None and notice_at <= current:
        errors.append("El aviso debe programarse en una fecha futura.")
    if event_at is not None and notice_at is not None and notice_at > event_at:
        errors.append("El aviso no puede ser posterior a la actividad.")

    location = _clean_text(data.get("location")) or None
    if location and len(location) > LOCATION_MAX_CHARS:
        errors.append(
            f"La ubicación no puede superar los {LOCATION_MAX_CHARS} caracteres."
        )

    link = _clean_text(data.get("link")) or None
    if link:
        if len(link) > LINK_MAX_CHARS:
            errors.append(f"El enlace no puede superar los {LINK_MAX_CHARS} caracteres.")
        elif not _URL_PATTERN.match(link) or not urlparse(link).netloc:
            errors.append("El enlace debe comenzar con http:// o https://.")

    cleaned = {
        "title": title,
        "category": category,
        "description": description,
        "location": location,
        "link": link,
        "event_at": event_at,
        "notice_at": notice_at,
    }
    return cleaned, errors


def category_label(category: str | None) -> str:
    return CATEGORY_LABELS.get(category or "", "Actividad")


def _template_text(value: Any, fallback: str) -> str:
    # Meta rechaza parámetros con saltos de línea, tabulaciones o más de
    # cuatro espacios seguidos: se colapsan a un solo espacio.
    text = " ".join(str(value or "").split())
    return text or fallback


def notice_place(event: dict[str, Any]) -> str:
    """Lugar que se informa en el aviso: ubicación, enlace o el respaldo."""
    return _template_text(
        event.get("location") or event.get("link"),
        NOTICE_PLACE_FALLBACK,
    )


def build_notice_template_parameters(
    event: dict[str, Any],
    organizer: str,
) -> list[str]:
    """Orden esperado en Meta: {{1}} municipio, {{2}} título, {{3}} categoría,
    {{4}} fecha y hora, {{5}} lugar o enlace."""
    return [
        _template_text(organizer, "Tu municipalidad"),
        _template_text(event.get("title"), "Actividad comunitaria"),
        category_label(event.get("category")),
        format_calendar_template_date(event["event_at"]),
        notice_place(event),
    ]
