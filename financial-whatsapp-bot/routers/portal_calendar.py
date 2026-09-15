"""API privada del calendario del panel del emprendedor."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from core.calendar_service import (
    ALLOWED_REMINDER_DAYS,
    calculate_reminder_at,
    get_calendar_timezone,
    normalize_local_event_at,
    parse_stored_datetime,
    prepare_event_schedule,
)
from db.calendar import (
    cancel_calendar_event,
    clear_calendar_session,
    complete_calendar_event,
    create_calendar_event,
    get_calendar_event,
    get_calendar_events_between,
    update_calendar_event,
)
from db.users import get_user
from schemas.calendar import (
    CalendarEventCreateRequest,
    CalendarEventResponse,
    CalendarEventUpdateRequest,
)
from services.portal_auth import get_session_phone, validate_csrf_token


logger = logging.getLogger("financial")

router = APIRouter(prefix="/portal/api/calendar", tags=["portal-calendar"])

_SESSION_COOKIE = "financial_session"
_MAX_RANGE_DAYS = 370


async def _authenticated_user(
    request: Request,
    session_id: str | None,
) -> dict:
    redis = request.app.state.redis
    if redis is None:
        raise HTTPException(
            status_code=503,
            detail="El acceso al panel no está disponible temporalmente",
        )

    phone = await get_session_phone(redis, session_id)
    if not phone:
        raise HTTPException(status_code=401, detail="La sesión venció")

    user = await run_in_threadpool(get_user, phone)
    if not user:
        raise HTTPException(status_code=404, detail="No encontramos el perfil")
    return user


async def _require_csrf(
    request: Request,
    session_id: str | None,
    submitted_token: str | None,
) -> None:
    redis = request.app.state.redis
    if redis is None or not await validate_csrf_token(
        redis,
        session_id,
        submitted_token,
    ):
        raise HTTPException(
            status_code=403,
            detail="La solicitud de calendario no es válida",
        )


def _reminder_days_before(event: dict) -> int:
    event_at = parse_stored_datetime(event["event_at"]).astimezone(
        get_calendar_timezone()
    )
    reminder_at = parse_stored_datetime(event["reminder_at"]).astimezone(
        get_calendar_timezone()
    )
    days = max(0, (event_at.date() - reminder_at.date()).days)
    return days if days in ALLOWED_REMINDER_DAYS else 0


def _serialize_event(event: dict) -> CalendarEventResponse:
    local_timezone = get_calendar_timezone()
    return CalendarEventResponse(
        id=str(event["id"]),
        description=event.get("description") or "Evento",
        event_at=parse_stored_datetime(event["event_at"]).astimezone(
            local_timezone
        ),
        reminder_at=parse_stored_datetime(event["reminder_at"]).astimezone(
            local_timezone
        ),
        reminder_days_before=_reminder_days_before(event),
        status=event.get("status") or "active",
    )


async def _clear_conversation_draft(user_id: str) -> None:
    """Evita dejar un borrador de WhatsApp obsoleto tras editar en la web."""
    await run_in_threadpool(clear_calendar_session, user_id)


@router.get("/events", response_model=list[CalendarEventResponse])
async def list_events(
    request: Request,
    start: datetime,
    end: datetime,
    financial_session: str | None = Cookie(
        default=None,
        alias=_SESSION_COOKIE,
    ),
):
    user = await _authenticated_user(request, financial_session)

    try:
        start_utc = normalize_local_event_at(start, require_future=False)
        end_utc = normalize_local_event_at(end, require_future=False)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if end_utc <= start_utc:
        raise HTTPException(
            status_code=400,
            detail="La fecha final debe ser posterior a la inicial",
        )
    if end_utc - start_utc > timedelta(days=_MAX_RANGE_DAYS):
        raise HTTPException(
            status_code=400,
            detail="El rango del calendario no puede superar 370 días",
        )

    events = await run_in_threadpool(
        get_calendar_events_between,
        user["id"],
        start_utc,
        end_utc,
    )
    return [_serialize_event(event) for event in events]


@router.post(
    "/events",
    response_model=CalendarEventResponse,
    status_code=201,
)
async def create_event(
    payload: CalendarEventCreateRequest,
    request: Request,
    financial_session: str | None = Cookie(
        default=None,
        alias=_SESSION_COOKIE,
    ),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    user = await _authenticated_user(request, financial_session)
    await _require_csrf(request, financial_session, csrf_token)

    try:
        event_at, reminder_at = prepare_event_schedule(
            payload.event_at,
            payload.reminder_days_before,
        )
        event = await run_in_threadpool(
            create_calendar_event,
            user["id"],
            payload.description,
            event_at,
            reminder_at,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if not event:
        raise HTTPException(status_code=500, detail="No se pudo guardar la fecha")

    await _clear_conversation_draft(user["id"])
    return _serialize_event(event)


@router.patch("/events/{event_id}", response_model=CalendarEventResponse)
async def edit_event(
    event_id: str,
    payload: CalendarEventUpdateRequest,
    request: Request,
    financial_session: str | None = Cookie(
        default=None,
        alias=_SESSION_COOKIE,
    ),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    user = await _authenticated_user(request, financial_session)
    await _require_csrf(request, financial_session, csrf_token)

    current_event = await run_in_threadpool(
        get_calendar_event,
        user["id"],
        event_id,
    )
    if not current_event or current_event.get("status") != "active":
        raise HTTPException(
            status_code=404,
            detail="El evento no existe o ya no está activo",
        )

    event_at: datetime | None = None
    reminder_at: datetime | None = None

    try:
        if payload.event_at is not None:
            reminder_days = (
                payload.reminder_days_before
                if payload.reminder_days_before is not None
                else _reminder_days_before(current_event)
            )
            event_at, reminder_at = prepare_event_schedule(
                payload.event_at,
                reminder_days,
            )
        elif payload.reminder_days_before is not None:
            event_at = parse_stored_datetime(current_event["event_at"])
            if event_at <= datetime.now(timezone.utc):
                raise ValueError(
                    "No puedes reprogramar un recordatorio para un evento vencido"
                )
            reminder_at = calculate_reminder_at(
                event_at,
                payload.reminder_days_before,
            )

        updated = await run_in_threadpool(
            update_calendar_event,
            user["id"],
            event_id,
            description=payload.description,
            event_at=event_at,
            reminder_at=reminder_at,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if not updated:
        raise HTTPException(
            status_code=404,
            detail="El evento no existe o ya no está activo",
        )

    await _clear_conversation_draft(user["id"])
    return _serialize_event(updated)


@router.delete("/events/{event_id}")
async def delete_event(
    event_id: str,
    request: Request,
    financial_session: str | None = Cookie(
        default=None,
        alias=_SESSION_COOKIE,
    ),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    user = await _authenticated_user(request, financial_session)
    await _require_csrf(request, financial_session, csrf_token)

    deleted = await run_in_threadpool(
        cancel_calendar_event,
        user["id"],
        event_id,
    )
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="El evento no existe o ya no está activo",
        )

    await _clear_conversation_draft(user["id"])
    return {"status": "cancelled", "event_id": event_id}


@router.post("/events/{event_id}/complete", response_model=CalendarEventResponse)
async def complete_event(
    event_id: str,
    request: Request,
    financial_session: str | None = Cookie(
        default=None,
        alias=_SESSION_COOKIE,
    ),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    user = await _authenticated_user(request, financial_session)
    await _require_csrf(request, financial_session, csrf_token)

    completed = await run_in_threadpool(
        complete_calendar_event,
        user["id"],
        event_id,
    )
    if not completed:
        raise HTTPException(
            status_code=404,
            detail="El evento no existe o ya no está activo",
        )

    await _clear_conversation_draft(user["id"])
    return _serialize_event(completed)
