"""API privada del calendario del panel del emprendedor."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

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
from core.alertas_tributarias import get_calendario_sii
from core.fondos import fund_applies_to_user
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
from db.fondos import list_active_funds_between
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
        source="personal",
        editable=event.get("status") == "active",
    )


def _serialize_tax_event(event: dict) -> CalendarEventResponse:
    """Convierte una obligación tributaria en un evento informativo.

    Estos eventos no se persisten en ``calendar_events`` ni pueden editarse:
    se calculan desde el calendario tributario ya utilizado por HdU07.
    """
    local_timezone = get_calendar_timezone()
    due_date: date = event["fecha_vencimiento"]
    event_at = datetime.combine(
        due_date,
        time.min,
        tzinfo=local_timezone,
    )
    event_type = event.get("tipo") or "tributaria"

    return CalendarEventResponse(
        id=f"tax:{event_type}:{due_date.isoformat()}",
        description=event.get("nombre") or "Fecha tributaria",
        event_at=event_at,
        reminder_at=event_at,
        reminder_days_before=0,
        status="informational",
        source="tributaria",
        editable=False,
        all_day=True,
        details=event.get("descripcion"),
        link=event.get("link"),
    )


def _tax_events_between(
    user: dict,
    start_at: datetime,
    end_at: datetime,
) -> list[CalendarEventResponse]:
    """Retorna fechas tributarias del rango para usuarios formalizados."""
    if user.get("inicio_sii") != "si":
        return []

    local_timezone = get_calendar_timezone()
    start_date = start_at.astimezone(local_timezone).date()
    end_date = end_at.astimezone(local_timezone).date()
    comuna = user.get("comuna")

    # El F29 de diciembre vence en enero del año siguiente. Por eso también
    # se genera el calendario del año anterior al comienzo del rango.
    events_by_id: dict[str, CalendarEventResponse] = {}
    for year in range(start_date.year - 1, end_date.year + 1):
        for event in get_calendario_sii(year, comuna):
            due_date = event["fecha_vencimiento"]
            if start_date <= due_date < end_date:
                serialized = _serialize_tax_event(event)
                events_by_id[serialized.id] = serialized

    return sorted(events_by_id.values(), key=lambda event: event.event_at)


def _serialize_fund_event(fund: dict) -> CalendarEventResponse:
    """Convierte el cierre de un fondo en un evento informativo."""
    local_timezone = get_calendar_timezone()
    raw_closing_date = fund.get("fecha_cierre")
    closing_date = (
        raw_closing_date
        if isinstance(raw_closing_date, date)
        else date.fromisoformat(str(raw_closing_date))
    )
    event_at = datetime.combine(
        closing_date,
        time.min,
        tzinfo=local_timezone,
    )
    fund_name = fund.get("nombre") or "Fondo concursable"
    emoji = fund.get("emoji") or "💰"
    entity = fund.get("entidad") or "apoyo al emprendimiento"
    details = [f"Convocatoria de {entity}."]
    amount = fund.get("monto_max")
    if amount:
        details.append(f"Financiamiento máximo: ${int(amount):,} CLP.".replace(",", "."))
    details.append("Revisa las bases y requisitos antes de postular.")
    stable_id = fund.get("id") or fund.get("slug") or fund_name

    return CalendarEventResponse(
        id=f"fund:{stable_id}:{closing_date.isoformat()}",
        description=f"{emoji} Cierre: {fund_name}",
        event_at=event_at,
        reminder_at=event_at,
        reminder_days_before=0,
        status="informational",
        source="fondo",
        editable=False,
        all_day=True,
        details=" ".join(details),
        link=fund.get("link"),
    )


def _fund_events_for_user(
    user: dict,
    funds: list[dict],
) -> list[CalendarEventResponse]:
    """Filtra fondos relevantes para un usuario no formalizado."""
    if user.get("inicio_sii") != "no":
        return []

    events = [
        _serialize_fund_event(fund)
        for fund in funds
        if fund_applies_to_user(fund, user)
    ]
    return sorted(events, key=lambda event: event.event_at)


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
    calendar_events = [_serialize_event(event) for event in events]
    calendar_events.extend(_tax_events_between(user, start_utc, end_utc))

    if user.get("inicio_sii") == "no":
        local_timezone = get_calendar_timezone()
        range_start = start_utc.astimezone(local_timezone).date()
        range_end = end_utc.astimezone(local_timezone).date()
        funds = await run_in_threadpool(
            list_active_funds_between,
            range_start,
            range_end,
        )
        calendar_events.extend(_fund_events_for_user(user, funds))

    return sorted(calendar_events, key=lambda event: event.event_at)


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
