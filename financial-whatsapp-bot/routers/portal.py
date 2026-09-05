"""
FinancIAl — routers/portal.py

Panel web del emprendedor (primer paso). Login sin contraseña: el
emprendedor pide su acceso desde el menú de WhatsApp ("📊 Ver mi panel
web"), recibe un link de un solo uso, y ese link crea una sesión válida
por 7 días (cookie). No se pide ni guarda ningún dato nuevo — la
identidad sigue siendo el mismo teléfono que ya usa con el bot.

Alcance de este primer paso: ver el estado actual (rubro, comuna, % de
roadmap, hito pendiente) y el historial completo de mensajes. Escribir
mensajes nuevos desde la web queda para una siguiente iteración.
"""
import html
import logging

from fastapi import APIRouter, Cookie, Request, Response

from core.roadmaps import get_pending_milestone
from db.users import get_messages, get_user
from services.portal_auth import create_session, get_session_phone, redeem_access_token

logger = logging.getLogger("financial")

router = APIRouter(prefix="/portal")

_SESSION_COOKIE = "financial_session"


def _pagina_base(titulo: str, contenido: str) -> str:
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titulo} · FinancIAl</title>
<style>
    * {{ box-sizing: border-box; }}
    body {{
        font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
        background: #f4f6f8;
        margin: 0;
        color: #1a1a1a;
    }}
    .contenedor {{
        max-width: 640px;
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
</style>
</head>
<body>
<div class="contenedor">
{contenido}
</div>
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

    return Response(
        content=_pagina_base("Mi panel", tarjeta_estado + tarjeta_historial),
        media_type="text/html",
    )
