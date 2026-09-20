"""
FinancIAl — routers/portal.py

Panel web del emprendedor (primer paso). Login sin contraseña: el
emprendedor pide su acceso desde el menú de WhatsApp ("📊 Ver mi panel
web"), recibe un link de un solo uso, y ese link crea una sesión válida
por 7 días (cookie). No se pide ni guarda ningún dato nuevo — la
identidad sigue siendo el mismo teléfono que ya usa con el bot.

El panel muestra el estado actual, roadmap, calendario, historial y resumen
financiero mensual en secciones independientes.
Escribir mensajes nuevos al asistente desde la web queda para otra iteración.
"""
import html
import logging
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Cookie, Request, Response

from core.alertas_tributarias import get_calendario_sii
from core.roadmaps import get_pending_milestone
from config import CALENDAR_DEFAULT_HOUR, REMINDER_TIMEZONE
from db.users import get_messages, get_user
from services.portal_auth import (
    create_session,
    get_or_create_csrf_token,
    get_session_phone,
    redeem_access_token,
)

logger = logging.getLogger("financial")

router = APIRouter(prefix="/portal")

_SESSION_COOKIE = "financial_session"
_PORTAL_TABS = (
    ("resumen", "Resumen"),
    ("calendario", "Calendario"),
    ("finanzas", "Finanzas"),
    ("chat", "Chat"),
)
_PORTAL_TAB_IDS = frozenset(tab_id for tab_id, _ in _PORTAL_TABS)
_MONTH_PATTERN = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")


def _portal_navigation(active_tab: str) -> str:
    links: list[str] = []
    for tab_id, label in _PORTAL_TABS:
        active_class = " active" if tab_id == active_tab else ""
        current_attribute = ' aria-current="page"' if tab_id == active_tab else ""
        links.append(
            f'<a class="portal-tab{active_class}" href="/portal?tab={tab_id}"'
            f'{current_attribute}>{html.escape(label)}</a>'
        )
    return (
        '<nav class="portal-tabs" aria-label="Secciones del panel">'
        + "".join(links)
        + "</nav>"
    )


def _current_portal_month() -> str:
    try:
        return datetime.now(ZoneInfo(REMINDER_TIMEZONE)).strftime("%Y-%m")
    except Exception:
        return date.today().strftime("%Y-%m")


def _pagina_base(
    titulo: str,
    contenido: str,
    *,
    head_extra: str = "",
    scripts: str = "",
    active_tab: str | None = None,
) -> str:
    navigation = _portal_navigation(active_tab) if active_tab else ""
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titulo} · FinancIAl</title>
<link rel="icon" type="image/svg+xml" href="/static/assets/Financial isotipo.svg">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
{head_extra}
<style>
    :root {{
        --petrol: #024655;
        --emerald: #50c887;
        --teal: #139381;
        --font-sans: "Plus Jakarta Sans", ui-sans-serif, system-ui, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
        font-family: var(--font-sans);
        background: color-mix(in srgb, var(--petrol) 5%, white);
        margin: 0;
        color: var(--petrol);
    }}
    .portal-topbar {{
        position: sticky;
        top: 0;
        z-index: 50;
        border-bottom: 1px solid color-mix(in srgb, var(--petrol) 20%, transparent);
        background: color-mix(in srgb, white 80%, transparent);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
    }}
    .portal-topbar-inner {{
        max-width: 1280px;
        margin: 0 auto;
        min-height: 68px;
        display: flex;
        align-items: center;
        padding: 16px 24px;
    }}
    .portal-brand {{
        display: flex;
        align-items: center;
        gap: 10px;
        text-decoration: none;
        color: var(--petrol);
    }}
    .portal-brand-logo {{ width: 36px; height: 36px; }}
    .portal-brand-text {{ display: flex; flex-direction: column; line-height: 1.2; }}
    .portal-brand-name {{ font-size: 16px; font-weight: 800; color: var(--petrol); }}
    .portal-brand-sub {{
        font-size: 10px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--petrol);
    }}
    .contenedor {{
        max-width: 1280px;
        margin: 0 auto;
        padding: 40px 24px 60px;
    }}
    .portal-tabs {{
        display: flex;
        gap: 8px;
        margin-bottom: 24px;
        padding: 6px;
        overflow-x: auto;
        background: color-mix(in srgb, white 88%, var(--petrol));
        border: 1px solid color-mix(in srgb, var(--petrol) 18%, transparent);
        border-radius: 14px;
    }}
    .portal-tab {{
        flex: 1 0 auto;
        min-width: 110px;
        padding: 10px 16px;
        border-radius: 10px;
        color: var(--petrol);
        font-size: 14px;
        font-weight: 700;
        text-align: center;
        text-decoration: none;
    }}
    .portal-tab:hover {{
        background: color-mix(in srgb, var(--emerald) 18%, white);
    }}
    .portal-tab.active {{
        background: var(--petrol);
        color: #fff;
        box-shadow: 0 6px 18px rgba(2, 70, 85, 0.16);
    }}
    @media (min-width: 1024px) {{
        .contenedor {{
            padding-left: 32px;
            padding-right: 32px;
        }}
        .portal-topbar-inner {{
            padding-left: 32px;
            padding-right: 32px;
        }}
    }}
    .tarjeta {{
        background: color-mix(in srgb, white 92%, var(--petrol));
        border: 1px solid color-mix(in srgb, var(--petrol) 20%, transparent);
        border-radius: 16px;
        padding: 24px;
        margin-bottom: 16px;
    }}
    h1 {{ font-size: 20px; font-weight: 800; letter-spacing: -0.025em; margin: 0 0 4px; color: var(--petrol); }}
    .subtitulo {{ color: color-mix(in srgb, var(--petrol) 90%, transparent); font-size: 14px; margin-bottom: 20px; }}
    .barra-fondo {{ background: color-mix(in srgb, var(--petrol) 12%, white); border-radius: 999px; height: 10px; overflow: hidden; }}
    .barra-progreso {{ background: var(--emerald); height: 100%; }}
    .chat-body {{
        background: color-mix(in srgb, var(--petrol) 5%, white);
        border-radius: 16px;
        padding: 16px 12px;
        display: flex;
        flex-direction: column;
        gap: 12px;
        max-height: 420px;
        overflow-y: auto;
    }}
    .mensaje {{
        max-width: 80%;
        padding: 10px 14px;
        font-size: 13px;
        line-height: 1.375;
        white-space: pre-wrap;
        word-wrap: break-word;
    }}
    .mensaje.usuario {{
        background: color-mix(in srgb, var(--emerald) 55%, white);
        color: var(--petrol);
        margin-left: auto;
        border-radius: 16px 16px 4px 16px;
    }}
    .mensaje.asistente {{
        background: color-mix(in srgb, var(--petrol) 8%, white);
        color: var(--petrol);
        margin-right: auto;
        border-radius: 16px 16px 16px 4px;
    }}
    .fecha {{ font-size: 11px; color: color-mix(in srgb, var(--petrol) 90%, transparent); margin: 2px 4px 12px; }}
    .aviso {{ text-align: center; margin-top: 60px; }}
    .hito {{
        display: flex;
        gap: 10px;
        padding: 10px 0;
        border-top: 1px solid color-mix(in srgb, var(--petrol) 20%, transparent);
    }}
    .hito:first-of-type {{ border-top: none; }}
    .hito.completado {{ opacity: 0.55; }}
    .hito.completado strong {{ text-decoration: line-through; }}
    .check {{ font-size: 18px; line-height: 1.3; }}
    .hito-desc {{ font-size: 13px; color: color-mix(in srgb, var(--petrol) 90%, transparent); margin-top: 2px; }}
    .evento {{
        display: flex;
        gap: 12px;
        padding: 10px 0;
        border-top: 1px solid color-mix(in srgb, var(--petrol) 20%, transparent);
    }}
    .evento:first-of-type {{ border-top: none; }}
    .evento-fecha {{
        min-width: 78px;
        font-weight: 700;
        font-size: 13px;
        color: var(--petrol);
    }}
    .evento.proximo .evento-fecha {{ color: #b91c1c; }}
    .evento.proximo {{ background: #fee2e2; margin: 0 -24px; padding: 10px 24px; }}
    .skip-link {{
        position: absolute;
        top: -100%;
        left: 16px;
        background: var(--emerald);
        color: var(--petrol);
        font-size: 14px;
        font-weight: 700;
        padding: 12px 20px;
        border-radius: 12px;
        text-decoration: none;
        z-index: 100;
    }}
    .skip-link:focus {{ top: 16px; }}
    a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible {{
        outline: 2px solid var(--petrol);
        outline-offset: 2px;
        border-radius: 4px;
    }}
</style>
</head>
<body>
<a class="skip-link" href="#main">Saltar al contenido principal</a>
<header class="portal-topbar">
    <div class="portal-topbar-inner">
        <a href="/portal" class="portal-brand">
            <img src="/static/assets/Financial isotipo.svg" alt="" class="portal-brand-logo">
            <span class="portal-brand-text">
                <span class="portal-brand-name">Financial</span>
                <span class="portal-brand-sub">Panel del emprendedor</span>
            </span>
        </a>
    </div>
</header>
<div class="contenedor" id="main" tabindex="-1">
{navigation}
{contenido}
</div>
{scripts}
</body>
</html>"""


def _pagina_no_autorizado(mensaje: str) -> Response:
    contenido = f"""
    <div class="aviso">
        <h2>🔒 {html.escape(mensaje)}</h2>
        <p>Pide tu link de acceso escribiéndole a FinancIAl por WhatsApp y
        tocando <strong>"📊 Ver mi panel web"</strong> en el menú.</p>
    </div>
    """
    return Response(
        content=_pagina_base("Acceso requerido", contenido),
        media_type="text/html",
        status_code=401,
    )


@router.get("/acceso")
async def acceso(token: str, request: Request):
    """Canjea el link de un solo uso por una sesión de panel."""
    redis = request.app.state.redis
    phone = await redeem_access_token(redis, token)

    if not phone:
        return _pagina_no_autorizado("Este link ya no es válido o venció")

    session_id = await create_session(redis, phone)

    response = Response(status_code=303, headers={"Location": "/portal"})
    response.set_cookie(
        _SESSION_COOKIE,
        session_id,
        max_age=7 * 24 * 3600,
        httponly=True,
        samesite="lax",
    )
    return response


def _tarjeta_roadmap(roadmap: list[dict]) -> str:
    """Lista completa de hitos (✅/⬜), no solo el pendiente."""
    if not roadmap:
        return ""

    filas = "\n".join(
        f'<div class="hito {"completado" if hito.get("done") else "pendiente"}">'
        f'<span class="check">{"✅" if hito.get("done") else "⬜"}</span>'
        f'<div><strong>{html.escape(hito.get("title") or "")}</strong>'
        f'<div class="hito-desc">{html.escape(hito.get("desc") or "")}</div></div>'
        f'</div>'
        for hito in roadmap
    )
    return f"""
    <div class="tarjeta">
        <h1>📋 Tu ruta de formalización</h1>
        {filas}
    </div>
    """


def _tarjeta_calendario(user: dict) -> str:
    """Calendario tributario personalizado (HdU07): próximos vencimientos
    del SII según la comuna del usuario. Solo aplica a formalizados — un
    no formalizado no tiene obligaciones tributarias todavía (mismo
    criterio que HdU07 CA2)."""
    if user.get("inicio_sii") != "si":
        return """
        <div class="tarjeta">
            <h1>📅 Calendario tributario</h1>
            <p class="subtitulo">Vas a ver acá tus fechas del SII (F29, F22, patente)
            una vez que completes tu formalización.</p>
        </div>
        """

    hoy = date.today()
    comuna = user.get("comuna")
    eventos = [
        evento for evento in get_calendario_sii(hoy.year, comuna)
        if evento["fecha_vencimiento"] >= hoy
    ]
    if hoy.month >= 11:
        eventos += [
            evento for evento in get_calendario_sii(hoy.year + 1, comuna)
            if evento["fecha_vencimiento"] >= hoy
        ]
    eventos.sort(key=lambda evento: evento["fecha_vencimiento"])

    filas = "\n".join(
        f'<div class="evento{" proximo" if (evento["fecha_vencimiento"] - hoy).days <= 5 else ""}">'
        f'<div class="evento-fecha">{evento["fecha_vencimiento"].strftime("%d/%m/%Y")}</div>'
        f'<div><strong>{html.escape(evento["nombre"])}</strong>'
        f'<div class="hito-desc">{html.escape(evento["descripcion"])}</div></div>'
        f'</div>'
        for evento in eventos[:12]
    )
    return f"""
    <div class="tarjeta">
        <h1>📅 Calendario tributario</h1>
        <p class="subtitulo">Tus próximas fechas clave del SII — en rojo, las que
        vencen en 5 días o menos.</p>
        {filas}
    </div>
    """


def _tarjeta_fechas_personales() -> str:
    """Contenedor del calendario interactivo de compromisos personales."""
    return f"""
    <div class="tarjeta calendario-tarjeta">
        <div class="calendar-header">
            <div>
                <h1>🗓️ Tu calendario del negocio</h1>
                <p class="subtitulo">Consulta tus fechas personales y obligaciones
                tributarias. También puedes agendar nuevos compromisos.</p>
            </div>
            <button
                type="button"
                id="calendar-create-button"
                class="calendar-primary-button"
            ><span class="calendar-button-plus">+</span> Nueva fecha</button>
        </div>

        <div id="calendar-message" role="status" aria-live="polite"></div>
        <div class="calendar-legend" aria-label="Tipos de fechas">
            <span><i class="calendar-legend-dot personal"></i> Fecha personal</span>
            <span><i class="calendar-legend-dot tax"></i> Fecha tributaria</span>
            <span><i class="calendar-legend-dot fund"></i> Fondo concursable</span>
            <span><i class="calendar-legend-dot completed"></i> Completada</span>
        </div>
        <div
            id="business-calendar"
            data-default-hour="{CALENDAR_DEFAULT_HOUR:02d}:00"
        ></div>
        <noscript>
            <p class="calendar-message error">
                Activa JavaScript para administrar tus fechas desde el panel.
            </p>
        </noscript>
    </div>

    <div
        id="calendar-modal-backdrop"
        class="calendar-modal-backdrop"
        aria-hidden="true"
    >
        <section
            class="calendar-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="calendar-modal-title"
        >
            <button
                type="button"
                id="calendar-modal-close"
                class="calendar-close-button"
                aria-label="Cerrar"
            >×</button>

            <h2 id="calendar-modal-title">Nueva fecha</h2>
            <form id="calendar-event-form">
                <input type="hidden" id="calendar-event-id">

                <label class="calendar-field">
                    <span>Descripción</span>
                    <input
                        type="text"
                        id="calendar-description"
                        maxlength="500"
                        placeholder="Ejemplo: renovar patente municipal"
                        required
                    >
                </label>

                <label class="calendar-field">
                    <span>Fecha y hora</span>
                    <input
                        type="datetime-local"
                        id="calendar-event-at"
                        required
                    >
                </label>

                <label class="calendar-field">
                    <span>Recordarme</span>
                    <select id="calendar-reminder-days">
                        <option value="0">El mismo día</option>
                        <option value="1">1 día antes</option>
                        <option value="3">3 días antes</option>
                        <option value="7">7 días antes</option>
                    </select>
                </label>

                <div
                    id="calendar-readonly-information"
                    class="calendar-readonly-information"
                    hidden
                >
                    <strong id="calendar-readonly-heading">Información</strong>
                    <p id="calendar-readonly-description"></p>
                    <a
                        id="calendar-readonly-link"
                        href="#"
                        target="_blank"
                        rel="noopener noreferrer"
                    >Abrir sitio oficial</a>
                </div>

                <div id="calendar-form-error" class="calendar-form-error"></div>

                <div class="calendar-modal-actions">
                    <button
                        type="button"
                        id="calendar-delete-button"
                        class="calendar-danger-button"
                    >Eliminar</button>
                    <button
                        type="button"
                        id="calendar-complete-button"
                        class="calendar-secondary-button"
                    >Marcar como completado</button>
                    <button
                        type="submit"
                        id="calendar-save-button"
                        class="calendar-primary-button"
                    >Guardar</button>
                </div>
            </form>
        </section>
    </div>
    """


def _tarjeta_finanzas(selected_month: str) -> str:
    """Contenedor de consulta mensual; los datos se obtienen por API privada."""
    safe_month = html.escape(selected_month, quote=True)
    return f"""
    <section
        class="tarjeta finance-dashboard"
        id="finance-dashboard"
        data-month="{safe_month}"
    >
        <div class="finance-header">
            <div>
                <h1>💰 Resumen financiero</h1>
                <p class="subtitulo">
                    Revisa los ingresos y gastos que registraste por WhatsApp.
                </p>
            </div>
            <label class="finance-month-field">
                <span>Mes</span>
                <input
                    type="month"
                    id="finance-month"
                    value="{safe_month}"
                >
            </label>
        </div>

        <div
            id="finance-status"
            class="finance-status"
            role="status"
            aria-live="polite"
        >Cargando movimientos…</div>

        <div id="finance-content" hidden>
            <div class="finance-summary-grid">
                <article class="finance-summary-card income">
                    <span>Ingresos</span>
                    <strong id="finance-income-total">$0</strong>
                </article>
                <article class="finance-summary-card expense">
                    <span>Gastos</span>
                    <strong id="finance-expense-total">$0</strong>
                </article>
                <article class="finance-summary-card net">
                    <span>Resultado neto</span>
                    <strong id="finance-net-total">$0</strong>
                </article>
                <article class="finance-summary-card movements">
                    <span>Movimientos</span>
                    <strong id="finance-movement-count">0</strong>
                </article>
            </div>

            <div id="finance-empty" class="finance-empty" hidden>
                <h2>Aún no tienes movimientos este mes</h2>
                <p>
                    Puedes comenzar escribiendo por WhatsApp, por ejemplo:
                    <em>“Hoy vendí $40.000 en empanadas”</em>.
                </p>
            </div>

            <div id="finance-details">
                <div class="finance-categories-grid">
                    <section class="finance-section">
                        <h2>Ingresos por categoría</h2>
                        <div id="finance-income-categories"></div>
                    </section>
                    <section class="finance-section">
                        <h2>Gastos por categoría</h2>
                        <div id="finance-expense-categories"></div>
                    </section>
                </div>

                <section class="finance-section finance-movements-section">
                    <h2>Movimientos del mes</h2>
                    <div class="finance-movement-list" id="finance-movement-list"></div>
                </section>
            </div>
        </div>

        <noscript>
            <p class="finance-status error">
                Activa JavaScript para consultar tu resumen financiero.
            </p>
        </noscript>
    </section>
    """


@router.get("")
async def panel(
    request: Request,
    tab: str = "resumen",
    month: str | None = None,
    financial_session: str | None = Cookie(default=None),
):
    """Panel del emprendedor organizado en secciones navegables."""
    redis = request.app.state.redis
    phone = await get_session_phone(redis, financial_session)

    if not phone:
        return _pagina_no_autorizado("Tu sesión venció o no has iniciado sesión")

    user = get_user(phone)
    if not user:
        return _pagina_no_autorizado("No encontramos tu perfil")

    active_tab = tab if tab in _PORTAL_TAB_IDS else "resumen"
    head_extra = ""
    scripts = ""

    if active_tab == "resumen":
        roadmap = user.get("roadmap") or []
        completados = sum(1 for hito in roadmap if hito.get("done"))
        total = len(roadmap)
        porcentaje = round((completados / total) * 100) if total else 0
        hito_pendiente = get_pending_milestone(user)

        rubro = html.escape(
            (user.get("rubro") or user.get("rubro_raw") or "tu negocio").capitalize()
        )
        comuna = html.escape(user.get("comuna") or "tu comuna")
        es_formalizado = user.get("inicio_sii") == "si"
        estado_sii = "✅ Formalizado" if es_formalizado else "⚠️ No formalizado"

        tarjeta_estado = f"""
        <div class="tarjeta">
            <h1>👋 Hola de nuevo</h1>
            <p class="subtitulo">{rubro} · {comuna} · {estado_sii}</p>
            {"" if es_formalizado else f'''
            <div class="barra-fondo"><div class="barra-progreso" style="width:{porcentaje}%"></div></div>
            <p class="subtitulo">{completados} de {total} hitos completados ({porcentaje}%)</p>
            '''}
            {f'<p><strong>👉 Tu siguiente paso:</strong> {html.escape(hito_pendiente["title"])}</p>' if hito_pendiente else ''}
        </div>
        """
        contenido = tarjeta_estado + _tarjeta_roadmap(roadmap)

    elif active_tab == "calendario":
        csrf_token = await get_or_create_csrf_token(redis, financial_session)
        contenido = _tarjeta_fechas_personales() + _tarjeta_calendario(user)
        head_extra = f"""
        <meta
            name="financial-csrf-token"
            content="{html.escape(csrf_token, quote=True)}"
        >
        <link rel="stylesheet" href="/static/portal_calendar.css">
        """
        scripts = """
        <script defer src="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.15/index.global.min.js"></script>
        <script defer src="https://cdn.jsdelivr.net/npm/@fullcalendar/core@6.1.15/locales-all.global.min.js"></script>
        <script defer src="/static/portal_calendar.js"></script>
        """

    elif active_tab == "finanzas":
        selected_month = (
            month
            if month and _MONTH_PATTERN.fullmatch(month)
            else _current_portal_month()
        )
        contenido = _tarjeta_finanzas(selected_month)
        head_extra = '<link rel="stylesheet" href="/static/portal_finances.css">'
        scripts = '<script defer src="/static/portal_finances.js"></script>'

    else:
        mensajes = get_messages(phone, limit=200)
        if mensajes:
            burbujas = '<div class="chat-body">' + "\n".join(
                f'<div class="mensaje {"usuario" if m.get("role") == "user" else "asistente"}">'
                f'{html.escape(m.get("content") or "")}</div>'
                for m in mensajes
            ) + "</div>"
        else:
            burbujas = (
                '<p class="subtitulo">Todavía no tienes mensajes con el '
                "asistente de IA.</p>"
            )

        contenido = f"""
        <div class="tarjeta">
            <h1>💬 Tu historial</h1>
            <p class="subtitulo">
                Las últimas {len(mensajes)} interacciones con el asistente.
            </p>
            {burbujas}
        </div>
        """

    return Response(
        content=_pagina_base(
            "Mi panel",
            contenido,
            head_extra=head_extra,
            scripts=scripts,
            active_tab=active_tab,
        ),
        media_type="text/html",
    )
