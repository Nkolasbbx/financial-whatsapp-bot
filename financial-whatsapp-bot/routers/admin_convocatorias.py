"""
FinancIAl — routers/admin_convocatorias.py

Convocatorias comunitarias (HdU019), lado del admin municipal. La página
reutiliza el calendario del panel del emprendedor (FullCalendar, modal y
estilos de portal_calendar.css): clic en un día para crear, clic en una
convocatoria para editarla o cancelarla. Los cambios van por una API JSON
con token CSRF, igual que el calendario del portal.

La comuna nunca viene del formulario: siempre es la de la cuenta admin,
para que un municipio no pueda avisar a usuarios de otro.
"""
import asyncio
import html
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Header, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool

from config import ADMIN_ACCOUNTS, CALENDAR_DEFAULT_HOUR
from core.calendar_service import get_calendar_timezone, normalize_local_event_at, parse_stored_datetime
from core.community_events import CATEGORIES, category_label, validate_community_event
from core.ics import build_community_ics
from db.community_events import (
    cancel_community_event,
    count_notice_recipients,
    create_community_event,
    get_community_event,
    get_delivery_summary,
    list_community_events_between,
    list_community_events_since,
    update_community_event,
)
from routers.admin import _pagina_base
from routers.portal import _versioned_static_url
from schemas.community_events import (
    CommunityEventAdminResponse,
    CommunityEventRequest,
    DeliverySummary,
)
from services.admin_auth import (
    get_admin_session_account,
    get_or_create_admin_csrf_token,
    validate_admin_csrf_token,
)
from services.google_calendar import account_sync_configured, sync_community_event

logger = logging.getLogger("financial")

router = APIRouter()

_MAX_RANGE_DAYS = 370
_FEED_PAST_DAYS = 90
_ICS_TOKEN_MIN_CHARS = 32
# Tope para esperar a Google dentro de la petición del admin; si se pasa, la
# convocatoria queda «pending» y el cron la sincroniza.
_GOOGLE_SYNC_TIMEOUT_SECONDS = 6


# ============================================================
# Utilidades
# ============================================================

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


def _notice_status(event: dict, now: datetime) -> str:
    if event.get("status") == "cancelled":
        return "cancelado"
    if event.get("notice_sent_at"):
        return "enviado"
    if parse_stored_datetime(event["notice_at"]) <= now:
        return "enviando"
    return "programado"


def _is_editable(event: dict, now: datetime) -> bool:
    """Editable mientras el aviso no haya llegado a su hora de envío."""
    return (
        event.get("status") == "scheduled"
        and not event.get("notice_sent_at")
        and parse_stored_datetime(event["notice_at"]) > now
    )


def _is_cancellable(event: dict, now: datetime) -> bool:
    return (
        event.get("status") == "scheduled"
        and parse_stored_datetime(event["event_at"]) > now
    )


def _google_status(event: dict, account: dict) -> str:
    if not account_sync_configured(account):
        return "disabled"
    return event.get("google_sync_status") or "pending"


def _serialize(
    event: dict,
    account: dict,
    deliveries: dict | None = None,
    now: datetime | None = None,
) -> CommunityEventAdminResponse:
    current = now or _utc_now()
    local_timezone = get_calendar_timezone()
    return CommunityEventAdminResponse(
        id=str(event["id"]),
        title=event.get("title") or "",
        category=event.get("category") or "otra",
        category_label=category_label(event.get("category")),
        description=event.get("description") or "",
        location=event.get("location"),
        link=event.get("link"),
        comuna=event.get("comuna") or account["comuna"],
        created_by=event.get("created_by") or "",
        event_at=parse_stored_datetime(event["event_at"]).astimezone(local_timezone),
        notice_at=parse_stored_datetime(event["notice_at"]).astimezone(local_timezone),
        status=event.get("status") or "scheduled",
        notice_status=_notice_status(event, current),
        deliveries=DeliverySummary(**(deliveries or {})),
        google_sync_status=_google_status(event, account),
        editable=_is_editable(event, current),
        cancellable=_is_cancellable(event, current),
    )


async def _api_account(request: Request, session_id: str | None) -> dict:
    account = await get_admin_session_account(request.app.state.redis, session_id)
    if not account:
        raise HTTPException(status_code=401, detail="Tu sesión venció. Vuelve a iniciar sesión.")
    return account


async def _require_csrf(request: Request, session_id: str | None, token: str | None) -> None:
    if not await validate_admin_csrf_token(request.app.state.redis, session_id, token):
        raise HTTPException(
            status_code=403,
            detail="La solicitud no es válida. Recarga la página e inténtalo de nuevo.",
        )


async def _sync_google(event: dict) -> None:
    """Replica en Google sin bloquear la respuesta más de unos segundos."""
    try:
        result = await asyncio.wait_for(
            sync_community_event(event),
            timeout=_GOOGLE_SYNC_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("Google Calendar tardó demasiado con la convocatoria %s", event.get("id"))
        result = "pending"
    if result != "disabled":
        event["google_sync_status"] = result


async def _load_event(event_id: str, comuna: str) -> dict:
    event = None
    if _valid_uuid(event_id):
        event = await run_in_threadpool(get_community_event, event_id, comuna)
    if not event:
        raise HTTPException(status_code=404, detail="La convocatoria no existe.")
    return event


def _validate_or_400(payload: CommunityEventRequest) -> dict:
    cleaned, errors = validate_community_event(payload.model_dump())
    if errors:
        raise HTTPException(status_code=400, detail=errors)
    return cleaned


# ============================================================
# Página
# ============================================================

def _account_for_ics_token(token: str) -> dict | None:
    for account in ADMIN_ACCOUNTS.values():
        expected = account.get("ics_token") or ""
        # Se comparan bytes: compare_digest no acepta str con caracteres no ASCII.
        if len(expected) >= _ICS_TOKEN_MIN_CHARS and secrets.compare_digest(
            token.encode("utf-8"),
            expected.encode("utf-8"),
        ):
            return account
    return None


def _public_base_url(request: Request) -> str:
    """URL pública del servidor, respetando el proxy de Railway (https)."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def _feed_url(request: Request, account: dict) -> str | None:
    token = account.get("ics_token") or ""
    if len(token) < _ICS_TOKEN_MIN_CHARS:
        return None
    return f"{_public_base_url(request)}/admin/convocatorias/feed/{quote(token, safe='')}.ics"


def _bloque_google(account: dict, feed_url: str | None) -> str:
    if account_sync_configured(account):
        sync_html = """
            <p><span class="admin-badge completo">Conectado</span></p>
            <p class="admin-hint">Cada convocatoria que crees, edites o canceles aquí se copia
            en segundos al Google Calendar del municipio.</p>"""
    else:
        sync_html = """
            <p><span class="admin-badge en-cola">No configurado</span></p>
            <p class="admin-hint">Cuando el equipo técnico lo configure, cada convocatoria se
            copiará automáticamente al Google Calendar del municipio.</p>"""

    if feed_url:
        webcal_url = "webcal://" + feed_url.split("://", 1)[1]
        google_url = "https://calendar.google.com/calendar/render?cid=" + quote(webcal_url, safe="")
        feed_html = f"""
            <div class="community-feed-row">
                <input type="text" id="community-feed-url" value="{html.escape(feed_url, quote=True)}"
                       readonly aria-label="Enlace del calendario de convocatorias">
                <button type="button" class="admin-btn-secondary" data-copy-target="community-feed-url">Copiar</button>
            </div>
            <p><a href="{html.escape(google_url, quote=True)}" target="_blank" rel="noopener noreferrer">Suscribirse en Google Calendar</a></p>
            <p class="admin-hint">Google actualiza las suscripciones cada 12 a 24 horas. También
            funciona en Outlook y Apple Calendar. Quien tenga este enlace puede ver las convocatorias
            de la comuna: no lo publiques.</p>"""
    else:
        feed_html = '<p class="admin-hint">La suscripción por enlace no está configurada para esta cuenta.</p>'

    return f"""
    <div class="admin-card">
        <h2>Google Calendar</h2>
        <div class="community-integrations">
            <section>
                <h3>Sincronización automática</h3>
                {sync_html}
            </section>
            <section>
                <h3>Suscripción por enlace (.ics)</h3>
                {feed_html}
            </section>
        </div>
    </div>"""


def _modal_html(comuna: str) -> str:
    opciones = "".join(
        f'<option value="{html.escape(slug)}">{html.escape(label)}</option>'
        for slug, label in CATEGORIES
    )
    return f"""
    <div id="community-modal-backdrop" class="calendar-modal-backdrop" aria-hidden="true">
        <section class="calendar-modal community-modal" role="dialog" aria-modal="true"
                 aria-labelledby="community-modal-title">
            <button type="button" id="community-modal-close" class="calendar-close-button"
                    aria-label="Cerrar">×</button>
            <h2 id="community-modal-title">Nueva convocatoria</h2>
            <form id="community-event-form" novalidate>
                <input type="hidden" id="community-event-id">

                <div id="community-status-info" class="calendar-readonly-information" hidden></div>

                <label class="calendar-field">
                    <span>Título</span>
                    <input type="text" id="community-title" maxlength="120" required
                           placeholder="Ejemplo: Taller de marketing digital">
                </label>

                <label class="calendar-field">
                    <span>Categoría</span>
                    <select id="community-category" required>
                        <option value="">Elige una categoría</option>
                        {opciones}
                    </select>
                </label>

                <label class="calendar-field">
                    <span>Descripción</span>
                    <textarea id="community-description" rows="4" maxlength="1000" required
                              placeholder="Qué se hará, a quién está dirigida y cómo participar"></textarea>
                </label>

                <label class="calendar-field">
                    <span>Comuna</span>
                    <input type="text" id="community-comuna" value="{html.escape(comuna, quote=True)}" readonly>
                </label>

                <div class="calendar-field-row">
                    <label class="calendar-field">
                        <span>Fecha y hora de la actividad</span>
                        <input type="datetime-local" id="community-event-at" required>
                    </label>
                    <label class="calendar-field">
                        <span>Fecha y hora del aviso</span>
                        <input type="datetime-local" id="community-notice-at" step="900" required>
                    </label>
                </div>
                <p class="calendar-hint" id="community-notice-hint"></p>

                <label class="calendar-field">
                    <span>Ubicación (opcional)</span>
                    <input type="text" id="community-location" maxlength="200"
                           placeholder="Ejemplo: Centro de Emprendimiento, Av. Recoleta 2774">
                </label>

                <label class="calendar-field">
                    <span>Enlace (opcional)</span>
                    <input type="url" id="community-link" maxlength="500"
                           placeholder="https://…">
                </label>

                <div id="community-form-error" class="calendar-form-error" role="alert"></div>

                <div class="calendar-modal-actions">
                    <button type="button" id="community-cancel-button"
                            class="calendar-danger-button">Cancelar convocatoria</button>
                    <button type="submit" id="community-save-button"
                            class="calendar-primary-button">Guardar</button>
                </div>
            </form>
        </section>
    </div>"""


def _pagina_convocatorias(account: dict, csrf_token: str, recipients: int, feed_url: str | None) -> str:
    comuna = account["comuna"]
    contenido = f"""
    <div class="admin-card calendario-tarjeta">
        <div class="calendar-header">
            <div>
                <h2>Convocatorias de {html.escape(comuna)}</h2>
                <p class="subtitulo">Haz clic en un día para crear una convocatoria o en una existente
                para editarla. Los emprendedores de {html.escape(comuna)} la verán en su calendario web
                y, si tienen los recordatorios activos, recibirán el aviso por WhatsApp.</p>
            </div>
            <button type="button" id="community-create-button" class="calendar-primary-button">
                <span class="calendar-button-plus">+</span> Nueva convocatoria
            </button>
        </div>

        <div id="calendar-message" role="status" aria-live="polite"></div>
        <div class="calendar-legend" aria-label="Estados de las convocatorias">
            <span><i class="calendar-legend-dot community"></i> Aviso programado</span>
            <span><i class="calendar-legend-dot community-sent"></i> Aviso enviado</span>
            <span><i class="calendar-legend-dot community-cancelled"></i> Cancelada</span>
        </div>
        <div
            id="community-calendar"
            data-default-hour="{CALENDAR_DEFAULT_HOUR:02d}:00"
            data-recipients="{int(recipients)}"
            data-google="{"on" if account_sync_configured(account) else "off"}"
        ></div>
        <noscript>
            <p class="calendar-message error">
                Activa JavaScript para administrar las convocatorias desde el panel.
            </p>
        </noscript>
    </div>
    {_bloque_google(account, feed_url)}
    {_modal_html(comuna)}
    """
    head_extra = f"""
    <meta name="financial-csrf-token" content="{html.escape(csrf_token, quote=True)}">
    <link rel="stylesheet" href="{_versioned_static_url('portal_calendar.css')}">
    """
    scripts = f"""
    <script defer src="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.15/index.global.min.js"></script>
    <script defer src="https://cdn.jsdelivr.net/npm/@fullcalendar/core@6.1.15/locales-all.global.min.js"></script>
    <script defer src="{_versioned_static_url('calendar_shared.js')}"></script>
    <script defer src="{_versioned_static_url('admin_convocatorias.js')}"></script>
    """
    return _pagina_base("Convocatorias", contenido, account, head_extra=head_extra, scripts=scripts)


@router.get("/admin/convocatorias")
async def pagina(
    request: Request,
    financial_admin_session: str | None = Cookie(default=None),
):
    redis = request.app.state.redis
    account = await get_admin_session_account(redis, financial_admin_session)
    if not account:
        return Response(status_code=303, headers={"Location": "/admin/login"})

    csrf_token = await get_or_create_admin_csrf_token(redis, financial_admin_session)
    recipients = await run_in_threadpool(count_notice_recipients, account["comuna"])
    return Response(
        content=_pagina_convocatorias(account, csrf_token, recipients, _feed_url(request, account)),
        media_type="text/html",
    )


@router.get("/admin/convocatorias/feed/{token}.ics")
async def feed_ics(token: str):
    """Feed iCal para suscribirse desde Google Calendar, Outlook o Apple.

    No usa la cookie de sesión (lo pide Google desde sus servidores): el
    token secreto de la URL identifica a la cuenta.
    """
    account = _account_for_ics_token(token)
    if not account:
        return Response(status_code=404)

    since = _utc_now() - timedelta(days=_FEED_PAST_DAYS)
    events = await run_in_threadpool(list_community_events_since, account["comuna"], since)
    body = build_community_ics(events, calendar_name=f"Convocatorias {account['comuna']}")
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, max-age=0",
            "Content-Disposition": 'inline; filename="convocatorias.ics"',
        },
    )


# ============================================================
# API JSON
# ============================================================

@router.get("/admin/api/convocatorias", response_model=list[CommunityEventAdminResponse])
async def listar(
    request: Request,
    start: datetime,
    end: datetime,
    financial_admin_session: str | None = Cookie(default=None),
):
    account = await _api_account(request, financial_admin_session)

    try:
        start_utc = normalize_local_event_at(start, require_future=False)
        end_utc = normalize_local_event_at(end, require_future=False)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if end_utc <= start_utc:
        raise HTTPException(status_code=400, detail="La fecha final debe ser posterior a la inicial")
    if end_utc - start_utc > timedelta(days=_MAX_RANGE_DAYS):
        raise HTTPException(status_code=400, detail="El rango del calendario no puede superar 370 días")

    events = await run_in_threadpool(
        list_community_events_between,
        account["comuna"],
        start_utc,
        end_utc,
        include_cancelled=True,
    )
    now = _utc_now()
    with_deliveries = [
        str(event["id"]) for event in events
        if _notice_status(event, now) in ("enviando", "enviado", "cancelado")
    ]
    summary = await run_in_threadpool(get_delivery_summary, with_deliveries) if with_deliveries else {}
    return [
        _serialize(event, account, summary.get(str(event["id"])), now)
        for event in events
    ]


@router.post(
    "/admin/api/convocatorias",
    response_model=CommunityEventAdminResponse,
    status_code=201,
)
async def crear(
    payload: CommunityEventRequest,
    request: Request,
    financial_admin_session: str | None = Cookie(default=None),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    account = await _api_account(request, financial_admin_session)
    await _require_csrf(request, financial_admin_session, csrf_token)
    cleaned = _validate_or_400(payload)

    event = await run_in_threadpool(
        create_community_event,
        account["comuna"],  # nunca desde el body: evita avisar a otra comuna
        account.get("nombre", account["comuna"]),
        cleaned,
    )
    if not event:
        raise HTTPException(status_code=500, detail="No se pudo guardar la convocatoria.")

    await _sync_google(event)
    return _serialize(event, account)


@router.patch(
    "/admin/api/convocatorias/{event_id}",
    response_model=CommunityEventAdminResponse,
)
async def editar(
    event_id: str,
    payload: CommunityEventRequest,
    request: Request,
    financial_admin_session: str | None = Cookie(default=None),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    account = await _api_account(request, financial_admin_session)
    await _require_csrf(request, financial_admin_session, csrf_token)

    current = await _load_event(event_id, account["comuna"])
    if current.get("status") == "cancelled":
        raise HTTPException(status_code=409, detail="La convocatoria está cancelada.")
    if not _is_editable(current, _utc_now()):
        raise HTTPException(
            status_code=409,
            detail="El aviso de esta convocatoria ya se envió o se está enviando: ya no se puede editar.",
        )

    cleaned = _validate_or_400(payload)
    updated = await run_in_threadpool(update_community_event, event_id, account["comuna"], cleaned)
    if not updated:
        raise HTTPException(
            status_code=409,
            detail="El aviso de esta convocatoria ya se envió o se está enviando: ya no se puede editar.",
        )

    await _sync_google(updated)
    return _serialize(updated, account)


@router.post(
    "/admin/api/convocatorias/{event_id}/cancelar",
    response_model=CommunityEventAdminResponse,
)
async def cancelar(
    event_id: str,
    request: Request,
    financial_admin_session: str | None = Cookie(default=None),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    account = await _api_account(request, financial_admin_session)
    await _require_csrf(request, financial_admin_session, csrf_token)

    current = await _load_event(event_id, account["comuna"])
    if current.get("status") == "cancelled":
        raise HTTPException(status_code=409, detail="La convocatoria ya estaba cancelada.")
    if not _is_cancellable(current, _utc_now()):
        raise HTTPException(status_code=409, detail="La actividad ya ocurrió: no se puede cancelar.")

    cancelled = await run_in_threadpool(cancel_community_event, event_id, account["comuna"])
    if not cancelled:
        raise HTTPException(status_code=409, detail="No se pudo cancelar la convocatoria.")

    await _sync_google(cancelled)
    summary = await run_in_threadpool(get_delivery_summary, [str(cancelled["id"])])
    return _serialize(cancelled, account, summary.get(str(cancelled["id"])))
