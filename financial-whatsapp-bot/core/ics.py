"""Feed iCalendar (RFC 5545) de las convocatorias comunitarias (HdU019).

Permite que un municipio se suscriba a sus convocatorias desde Google
Calendar («Desde URL»), Outlook o Apple Calendar sin configurar nada en
Google. Se genera a mano para no sumar dependencias: el formato que se usa
es pequeño (VCALENDAR con VEVENT de texto).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.calendar_service import parse_stored_datetime
from core.community_events import category_label

DEFAULT_DURATION = timedelta(hours=1)
_MAX_LINE_OCTETS = 75
# SEQUENCE es un entero de 32 bits: se cuenta desde 2026 y no desde 1970.
_SEQUENCE_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def escape_text(value: str) -> str:
    """Escapa un valor TEXT: barra invertida, punto y coma, coma y saltos."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold_line(line: str) -> str:
    """Pliega una línea en tramos de 75 octetos sin cortar caracteres UTF-8.

    Las líneas de continuación empiezan con un espacio, que también cuenta
    dentro de los 75 octetos.
    """
    if len(line.encode("utf-8")) <= _MAX_LINE_OCTETS:
        return line

    parts: list[str] = []
    current = ""
    current_size = 0
    limit = _MAX_LINE_OCTETS
    for char in line:
        size = len(char.encode("utf-8"))
        if current_size + size > limit:
            parts.append(current)
            current, current_size = char, size
            limit = _MAX_LINE_OCTETS - 1
        else:
            current += char
            current_size += size
    parts.append(current)
    return "\r\n ".join(parts)


def _utc_stamp(value: str | datetime) -> str:
    return parse_stored_datetime(value).strftime("%Y%m%dT%H%M%SZ")


def _sequence(event: dict) -> int:
    updated = event.get("updated_at") or event.get("created_at")
    if not updated:
        return 0
    return max(0, int((parse_stored_datetime(updated) - _SEQUENCE_EPOCH).total_seconds()))


def _event_lines(event: dict, now: datetime) -> list[str]:
    start = parse_stored_datetime(event["event_at"])
    description = event.get("description") or ""
    if event.get("link"):
        description += f"\n\nEnlace: {event['link']}"
    if event.get("created_by"):
        description += f"\nOrganiza: {event['created_by']}"

    lines = [
        "BEGIN:VEVENT",
        f"UID:{event['id']}@financial",
        f"DTSTAMP:{_utc_stamp(now)}",
        f"DTSTART:{_utc_stamp(start)}",
        f"DTEND:{_utc_stamp(start + DEFAULT_DURATION)}",
        f"SUMMARY:{escape_text(event.get('title') or 'Convocatoria comunitaria')}",
        f"DESCRIPTION:{escape_text(description.strip())}",
        f"CATEGORIES:{escape_text(category_label(event.get('category')))}",
        f"SEQUENCE:{_sequence(event)}",
        # Las canceladas se publican como CANCELLED para que desaparezcan
        # también del calendario de quien ya estaba suscrito.
        "STATUS:" + ("CANCELLED" if event.get("status") == "cancelled" else "CONFIRMED"),
    ]
    if event.get("location"):
        lines.append(f"LOCATION:{escape_text(event['location'])}")
    if event.get("link"):
        lines.append(f"URL:{event['link']}")
    if event.get("updated_at"):
        lines.append(f"LAST-MODIFIED:{_utc_stamp(event['updated_at'])}")
    lines.append("END:VEVENT")
    return lines


def build_community_ics(
    events: list[dict],
    *,
    calendar_name: str,
    now: datetime | None = None,
) -> str:
    """Arma el archivo .ics completo con líneas terminadas en CRLF."""
    current = now or datetime.now(timezone.utc)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//FinancIAl//Convocatorias comunitarias//ES",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text(calendar_name)}",
        "X-WR-TIMEZONE:America/Santiago",
        # Sugerencia de refresco: Google la ignora (actualiza cada 12-24 h),
        # pero Apple y Outlook sí la respetan.
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    for event in events:
        lines.extend(_event_lines(event, current))
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_line(line) for line in lines) + "\r\n"
