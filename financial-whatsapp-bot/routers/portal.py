"""
FinancIAl — routers/portal.py

Panel web del emprendedor (primer paso). Login sin contraseña: el
emprendedor pide su acceso desde el menú de WhatsApp ("📊 Ver mi panel
web"), recibe un link de un solo uso, y ese link crea una sesión válida
por 7 días (cookie). No se pide ni guarda ningún dato nuevo — la
identidad sigue siendo el mismo teléfono que ya usa con el bot.

El panel muestra el estado actual (rubro, comuna, roadmap e historial) y
permite administrar las fechas importantes del calendario personalizado.
Escribir mensajes nuevos al asistente desde la web queda para otra iteración.
"""
import html
import logging
from datetime import date

from fastapi import APIRouter, Cookie, Request, Response

from core.alertas_tributarias import get_calendario_sii
from core.roadmaps import get_pending_milestone
from config import CALENDAR_DEFAULT_HOUR
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


def _pagina_base(
    titulo: str,
    contenido: str,
    *,
    head_extra: str = "",
    scripts: str = "",
) -> str:
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titulo} · FinancIAl</title>
{head_extra}
<style>
    * {{ box-sizing: border-box; }}
    body {{
        font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
        background: #f4f6f8;
        margin: 0;
        color: #1a1a1a;
    }}
    .contenedor {{
        max-width: 1050px;
        margin: 0 auto;
        padding: 24px 16px 60px;
    }}
    .tarjeta {{
        background: white;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 16px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }}
    h1 {{ font-size: 20px; margin: 0 0 4px; }}
    .subtitulo {{ color: #667085; font-size: 14px; margin-bottom: 20px; }}
    .barra-fondo {{ background: #e4e7ec; border-radius: 999px; height: 10px; overflow: hidden; }}
    .barra-progreso {{ background: #16a34a; height: 100%; }}
    .mensaje {{
        max-width: 80%;
        padding: 10px 14px;
        border-radius: 14px;
        margin-bottom: 8px;
        font-size: 14px;
        line-height: 1.4;
        white-space: pre-wrap;
        word-wrap: break-word;
    }}
    .mensaje.usuario {{
        background: #dcf8c6;
        margin-left: auto;
        border-bottom-right-radius: 2px;
    }}
    .mensaje.asistente {{
        background: #f0f1f3;
        margin-right: auto;
        border-bottom-left-radius: 2px;
    }}
    .fecha {{ font-size: 11px; color: #98a2b3; margin: 2px 4px 12px; }}
    .aviso {{ text-align: center; margin-top: 60px; }}
    .hito {{
        display: flex;
        gap: 10px;
        padding: 10px 0;
        border-top: 1px solid #eef0f2;
    }}
    .hito:first-of-type {{ border-top: none; }}
    .hito.completado {{ opacity: 0.55; }}
    .hito.completado strong {{ text-decoration: line-through; }}
    .check {{ font-size: 18px; line-height: 1.3; }}
    .hito-desc {{ font-size: 13px; color: #667085; margin-top: 2px; }}
    .evento {{
        display: flex;
        gap: 12px;
        padding: 10px 0;
        border-top: 1px solid #eef0f2;
    }}
    .evento:first-of-type {{ border-top: none; }}
    .evento-fecha {{
        min-width: 78px;
        font-weight: 600;
        font-size: 13px;
        color: #344054;
    }}
    .evento.proximo .evento-fecha {{ color: #b42318; }}
    .evento.proximo {{ background: #fef3f2; margin: 0 -20px; padding: 10px 20px; }}
</style>
</head>
<body>
<div class="contenedor">
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
                <h1>🗓️ Tus fechas importantes</h1>
                <p class="subtitulo">Agenda y administra compromisos relacionados
                con tu negocio. También podrás recibir el aviso por WhatsApp.</p>
            </div>
            <button
                type="button"
                id="calendar-create-button"
                class="calendar-primary-button"
            >+ Nueva fecha</button>
        </div>

        <div id="calendar-message" role="status" aria-live="polite"></div>
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


@router.get("")
async def panel(
    request: Request,
    financial_session: str | None = Cookie(default=None),
):
    """Panel del emprendedor: estado actual + historial completo."""
    redis = request.app.state.redis
    phone = await get_session_phone(redis, financial_session)

    if not phone:
        return _pagina_no_autorizado("Tu sesión venció o no has iniciado sesión")

    user = get_user(phone)
    if not user:
        return _pagina_no_autorizado("No encontramos tu perfil")

    csrf_token = await get_or_create_csrf_token(redis, financial_session)

    roadmap = user.get("roadmap") or []
    completados = sum(1 for hito in roadmap if hito.get("done"))
    total = len(roadmap)
    porcentaje = round((completados / total) * 100) if total else 0
    hito_pendiente = get_pending_milestone(user)

    rubro = html.escape((user.get("rubro") or user.get("rubro_raw") or "tu negocio").capitalize())
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

    mensajes = get_messages(phone, limit=200)
    if mensajes:
        burbujas = "\n".join(
            f'<div class="mensaje {"usuario" if m.get("role") == "user" else "asistente"}">'
            f'{html.escape(m.get("content") or "")}</div>'
            for m in mensajes
        )
    else:
        burbujas = '<p class="subtitulo">Todavía no tienes mensajes con el asistente de IA.</p>'

    tarjeta_historial = f"""
    <div class="tarjeta">
        <h1>💬 Tu historial</h1>
        <p class="subtitulo">Las últimas {len(mensajes)} interacciones con el asistente.</p>
        {burbujas}
    </div>
    """

    contenido = (
        tarjeta_estado
        + _tarjeta_roadmap(roadmap)
        + _tarjeta_fechas_personales()
        + _tarjeta_calendario(user)
        + tarjeta_historial
    )

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

    return Response(
        content=_pagina_base(
            "Mi panel",
            contenido,
            head_extra=head_extra,
            scripts=scripts,
        ),
        media_type="text/html",
    )
