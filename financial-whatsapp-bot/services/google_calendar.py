"""Réplica de las convocatorias en Google Calendar (HdU019).

Integración de ida (panel → Google) con una cuenta de servicio: cada
municipio comparte su calendario con el correo de la cuenta de servicio
(«Hacer cambios en eventos») y configura el ID del calendario en su cuenta
admin (ver ADMIN_ACCOUNTS en config.py). No hay pantalla de login de Google
ni tokens por usuario.

google-auth solo se usa para firmar el JWT y obtener el access token; las
llamadas REST van con httpx, igual que el resto del proyecto. Lo que se
edite directamente en Google no vuelve al panel.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from datetime import timedelta
from urllib.parse import quote

import httpx

from config import (
    ADMIN_ACCOUNTS,
    GOOGLE_CALENDAR_SYNC_ENABLED,
    GOOGLE_SERVICE_ACCOUNT_JSON,
    REMINDER_TIMEZONE,
)
from core.calendar_service import get_calendar_timezone, parse_stored_datetime
from core.community_events import category_label
from db.community_events import list_events_pending_google_sync, set_google_sync_result
from services.calendar_reminders import format_calendar_template_date

logger = logging.getLogger("financial")

GOOGLE_SCOPES = ("https://www.googleapis.com/auth/calendar.events",)
_API_BASE = "https://www.googleapis.com/calendar/v3"
_HTTP_TIMEOUT_SECONDS = 5.0
# La HdU no pide hora de término: en Google se muestra un bloque de 1 hora.
DEFAULT_DURATION = timedelta(hours=1)
SYNC_BATCH_SIZE = 20

_credentials = None
_credentials_lock = threading.Lock()


class GoogleCalendarError(RuntimeError):
    pass


def sync_enabled() -> bool:
    return GOOGLE_CALENDAR_SYNC_ENABLED and bool(GOOGLE_SERVICE_ACCOUNT_JSON)


def calendar_id_for_comuna(comuna: str | None) -> str | None:
    for account in ADMIN_ACCOUNTS.values():
        if account.get("comuna") == comuna and account.get("google_calendar_id"):
            return account["google_calendar_id"]
    return None


def account_sync_configured(account: dict) -> bool:
    return sync_enabled() and bool(account.get("google_calendar_id"))


def google_event_id(event_id: str) -> str:
    """Id del evento en Google derivado del uuid de la convocatoria.

    Google acepta ids propios en base32hex (a-v, 0-9) y el hex del uuid es
    un subconjunto: así insertar es idempotente y no hace falta guardar el
    id de Google en la BD.
    """
    return uuid.UUID(str(event_id)).hex


def _access_token_sync() -> str:
    global _credentials

    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    with _credentials_lock:
        if _credentials is None:
            info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
            _credentials = service_account.Credentials.from_service_account_info(
                info,
                scopes=list(GOOGLE_SCOPES),
            )
        if not _credentials.valid:
            _credentials.refresh(Request())
        return _credentials.token


async def _access_token() -> str:
    return await asyncio.to_thread(_access_token_sync)


def build_google_event(event: dict) -> dict:
    """Cuerpo del evento para la API de Google Calendar."""
    local_timezone = get_calendar_timezone()
    start = parse_stored_datetime(event["event_at"]).astimezone(local_timezone)
    end = start + DEFAULT_DURATION

    description_lines = [event.get("description") or ""]
    if event.get("link"):
        description_lines += ["", f"Enlace: {event['link']}"]
    description_lines += [
        "",
        f"Aviso por WhatsApp: {format_calendar_template_date(event['notice_at'])}",
        "Publicado desde el panel municipal de FinancIAl.",
    ]

    body = {
        "id": google_event_id(event["id"]),
        "summary": f"[{category_label(event.get('category'))}] {event.get('title') or ''}".strip(),
        "description": "\n".join(description_lines).strip(),
        "start": {"dateTime": start.isoformat(), "timeZone": REMINDER_TIMEZONE},
        "end": {"dateTime": end.isoformat(), "timeZone": REMINDER_TIMEZONE},
        "extendedProperties": {"private": {"financial_id": str(event["id"])}},
    }
    if event.get("location"):
        body["location"] = event["location"]
    if event.get("link"):
        body["source"] = {"title": "Enlace de la actividad", "url": event["link"]}
    return body


def _events_url(calendar_id: str) -> str:
    return f"{_API_BASE}/calendars/{quote(calendar_id, safe='')}/events"


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code >= 400:
        raise GoogleCalendarError(
            f"Google Calendar respondió {response.status_code}: {response.text[:500]}"
        )


async def upsert_google_event(
    event: dict,
    calendar_id: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Crea o actualiza el evento en Google."""
    token = await _access_token()
    headers = {"Authorization": f"Bearer {token}"}
    body = build_google_event(event)
    url = _events_url(calendar_id)

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS, transport=transport) as client:
        response = await client.post(url, json=body, headers=headers)
        if response.status_code == 409:
            # El id ya existe (o fue eliminado antes: Google lo conserva).
            # PATCH lo actualiza y, con status confirmed, lo reactiva.
            patch_body = {key: value for key, value in body.items() if key != "id"}
            patch_body["status"] = "confirmed"
            response = await client.patch(
                f"{url}/{body['id']}",
                json=patch_body,
                headers=headers,
            )
        _raise_for_status(response)


async def delete_google_event(
    event: dict,
    calendar_id: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Elimina el evento en Google; si ya no existe, no es un error."""
    token = await _access_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{_events_url(calendar_id)}/{google_event_id(event['id'])}"

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS, transport=transport) as client:
        response = await client.delete(url, headers=headers)
    if response.status_code in (404, 410):
        return
    _raise_for_status(response)


async def sync_community_event(event: dict) -> str:
    """Replica o elimina una convocatoria en Google y guarda el resultado.

    Nunca lanza excepciones: un fallo de Google no debe impedir crear,
    editar ni cancelar la convocatoria. Los fallos quedan en `failed` y el
    cron de convocatorias los reintenta.
    """
    if not sync_enabled():
        return "disabled"

    calendar_id = calendar_id_for_comuna(event.get("comuna"))
    if not calendar_id:
        status, error = "disabled", None
    else:
        try:
            if event.get("status") == "cancelled":
                await delete_google_event(event, calendar_id)
            else:
                await upsert_google_event(event, calendar_id)
            status, error = "synced", None
        except Exception as exc:
            logger.warning(
                "No se pudo sincronizar la convocatoria %s con Google Calendar: %s",
                event.get("id"),
                exc,
            )
            status, error = "failed", str(exc)

    try:
        await asyncio.to_thread(set_google_sync_result, event["id"], status, error)
    except Exception:
        logger.exception(
            "No se pudo guardar el estado de Google de la convocatoria %s",
            event.get("id"),
        )
    return status


async def sync_pending_google_events() -> dict:
    """Reintenta las réplicas pendientes o fallidas (cron de convocatorias)."""
    if not sync_enabled():
        return {"google_status": "disabled"}

    events = await asyncio.to_thread(list_events_pending_google_sync, SYNC_BATCH_SIZE)
    counters = {"google_status": "completed", "synced": 0, "failed": 0, "disabled": 0}
    for event in events:
        result = await sync_community_event(event)
        counters[result] = counters.get(result, 0) + 1
    return counters
