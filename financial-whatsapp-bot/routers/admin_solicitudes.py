"""
FinancIAl — routers/admin_solicitudes.py

Solicitudes de contacto con un experto (HdU16), lado del admin municipal.
El admin ve las solicitudes de SU comuna, toma una y conversa con el
emprendedor desde el panel. Los mensajes salen y entran por el número del
bot: el teléfono del emprendedor nunca se renderiza acá, y el emprendedor
nunca ve el del admin.
"""
import html
import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Form, Request, Response

from core.calendar_service import get_calendar_timezone
from core.roadmaps import get_pending_milestone
from db.expert_requests import (
    add_expert_message,
    get_expert_request,
    list_expert_messages,
    list_expert_requests,
    resolve_expert_request,
    take_expert_request,
)
from routers.admin import _flash_html, _pagina_base, _pop_flash, _set_flash
from services.admin_auth import get_admin_session_account
from services.whatsapp import WhatsAppAPIError, send_text

logger = logging.getLogger("financial")

router = APIRouter(prefix="/admin/solicitudes")

WHATSAPP_WINDOW = timedelta(hours=24)
MAX_MESSAGE_CHARS = 1000

_ESTADOS = {
    "pending": ("Pendiente", "en-cola"),
    "in_progress": ("En atención", "procesando"),
    "resolved": ("Resuelta", "listo"),
}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    # Postgres puede devolver 1-6 decimales; fromisoformat (3.10) exige 3 o 6.
    text = re.sub(r"\.(\d+)", lambda m: "." + m.group(1).ljust(6, "0")[:6], text)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _alias(request_row: dict) -> str:
    return f"Emprendedor {str(request_row['id'])[:4].upper()}"


def _fecha_local(value: str | None) -> str:
    parsed = _parse_dt(value)
    if not parsed:
        return "—"
    return parsed.astimezone(get_calendar_timezone()).strftime("%d-%m-%Y %H:%M")


def window_remaining(request_row: dict, now: datetime | None = None) -> timedelta:
    """Tiempo que queda para escribirle texto libre (24 h desde su último mensaje)."""
    last = _parse_dt(request_row.get("user_last_message_at"))
    if not last:
        return timedelta(0)
    now = now or datetime.now(timezone.utc)
    return max(timedelta(0), last + WHATSAPP_WINDOW - now)


def _window_text(remaining: timedelta) -> str:
    if remaining <= timedelta(0):
        return "Ventana cerrada"
    horas, resto = divmod(int(remaining.total_seconds()), 3600)
    return f"{horas} h {resto // 60} min restantes"


async def _auth(request: Request, session: str | None) -> dict | None:
    return await get_admin_session_account(request.app.state.redis, session)


def _redirect(path: str) -> Response:
    return Response(status_code=303, headers={"Location": path})


def _badge(status: str) -> str:
    label, clase = _ESTADOS.get(status, (status, "en-cola"))
    return f'<span class="admin-badge {clase}">{html.escape(label)}</span>'


def _hace(value: str | None) -> str:
    parsed = _parse_dt(value)
    if not parsed:
        return ""
    minutos = int((datetime.now(timezone.utc) - parsed).total_seconds() // 60)
    if minutos < 1:
        return "hace instantes"
    if minutos < 60:
        return f"hace {minutos} min"
    if minutos < 24 * 60:
        return f"hace {minutos // 60} h"
    return f"hace {minutos // (24 * 60)} d"


def _ventana_html(restante: timedelta) -> str:
    if restante <= timedelta(0):
        clase = "closed"
    elif restante < timedelta(hours=6):
        clase = "warn"
    else:
        clase = "ok"
    return f'<span class="admin-window-{clase}">{html.escape(_window_text(restante))}</span>'


def _tabla(titulo: str, filas: list[dict], vacio: str) -> str:
    cuerpo = "\n".join(
        f"""<tr>
            <td><strong>{html.escape(_alias(r))}</strong></td>
            <td>{html.escape((r.get("users") or {}).get("rubro") or "—")}</td>
            <td>{html.escape(_fecha_local(r.get("created_at")))}<div class="admin-wait">{html.escape(_hace(r.get("created_at")))}</div></td>
            <td>{_badge(r["status"])}</td>
            <td>{_ventana_html(window_remaining(r)) if r["status"] != "resolved" else "—"}</td>
            <td><a class="admin-btn-secondary" href="/admin/solicitudes/{html.escape(str(r["id"]))}">Abrir</a></td>
        </tr>"""
        for r in filas
    ) or f'<tr><td colspan="6">{html.escape(vacio)}</td></tr>'
    conteo = f" ({len(filas)})" if filas else ""
    return f"""
    <div class="admin-card">
        <h2>{html.escape(titulo)}{conteo}</h2>
        <table class="admin-table">
            <thead><tr><th>Emprendedor</th><th>Rubro</th><th>Solicitada</th><th>Estado</th><th>Ventana WhatsApp</th><th></th></tr></thead>
            <tbody>{cuerpo}</tbody>
        </table>
    </div>"""


@router.get("")
async def lista(request: Request, financial_admin_session: str | None = Cookie(default=None)):
    account = await _auth(request, financial_admin_session)
    if not account:
        return _redirect("/admin/login")

    comuna = account["comuna"]
    flash = await _pop_flash(request.app.state.redis, financial_admin_session)
    contenido = (
        _flash_html(flash)
        + _tabla("Pendientes", list_expert_requests(comuna, ("pending",)), "No hay solicitudes pendientes.")
        + _tabla("En atención", list_expert_requests(comuna, ("in_progress",)), "No hay conversaciones en curso.")
        + _tabla("Resueltas (recientes)", list_expert_requests(comuna, ("resolved",), limit=10), "Todavía no hay solicitudes resueltas.")
    )
    return Response(content=_pagina_base("Solicitudes de experto", contenido, account), media_type="text/html")


def _hilo_html(mensajes: list[dict]) -> str:
    if not mensajes:
        return '<p class="admin-thread-empty">Todavía no hay mensajes en esta conversación.</p>'
    return "\n".join(
        f'<div class="admin-msg admin-msg-{"expert" if m["sender"] == "expert" else "user"}">'
        f'<span class="admin-msg-meta">{"Tú" if m["sender"] == "expert" else "Emprendedor"} · {html.escape(_fecha_local(m.get("created_at")))}</span>'
        f'<p>{html.escape(m["content"])}</p></div>'
        for m in mensajes
    )


def _bloque_tomar(request_id: str, nombre: str, restante: timedelta) -> str:
    if restante <= timedelta(0):
        return """
    <div class="admin-card">
        <h2>No se puede tomar todavía</h2>
        <p class="admin-error">La ventana de 24 h de WhatsApp ya se cerró: no se le puede escribir hasta que el emprendedor vuelva a escribir.</p>
    </div>"""
    return f"""
    <div class="admin-card admin-card-feature">
        <h2>Tomar esta solicitud</h2>
        <p class="subtitulo">Esto es lo que pasa al tomarla:</p>
        <ol class="admin-steps">
            <li><span>El emprendedor recibe un aviso de que un experto de <strong>{html.escape(nombre)}</strong> lo atenderá por su chat de WhatsApp.</span></li>
            <li><span>El asistente de IA queda en pausa solo para él, hasta que cierres la conversación.</span></li>
            <li><span>Puedes escribirle desde aquí mientras la ventana de WhatsApp siga abierta ({_ventana_html(restante)}).</span></li>
        </ol>
        <form method="post" action="/admin/solicitudes/{html.escape(request_id)}/tomar">
            <button type="submit" class="admin-btn">Tomar solicitud</button>
        </form>
    </div>"""


def _bloque_conversacion(request_id: str, mensajes: list[dict], restante: timedelta, nombre: str) -> str:
    if restante > timedelta(0):
        composer = f"""
        <form method="post" action="/admin/solicitudes/{html.escape(request_id)}/mensaje" class="admin-compose">
            <label for="mensaje">Escribe tu respuesta</label>
            <textarea id="mensaje" name="mensaje" rows="3" maxlength="{MAX_MESSAGE_CHARS}" required
                      placeholder="El emprendedor lo recibe en su WhatsApp como «{html.escape(nombre)}»"></textarea>
            <div class="admin-form-actions">
                <button type="submit" class="admin-btn">Enviar mensaje</button>
            </div>
        </form>"""
    else:
        composer = '<p class="admin-error">La ventana de 24 h de WhatsApp está cerrada: no se puede enviar texto libre hasta que el emprendedor vuelva a escribir.</p>'
    cerrar = f"""
        <form method="post" action="/admin/solicitudes/{html.escape(request_id)}/resolver"
              onsubmit="return confirm('¿Marcar como resuelta? El emprendedor vuelve a hablar con el asistente.')">
            <div class="admin-form-actions">
                <span class="admin-hint">¿Ya resolviste su consulta?</span>
                <button type="submit" class="admin-btn-secondary">Marcar como resuelta</button>
            </div>
        </form>"""
    return f"""
    <div class="admin-card">
        <h2>Conversación</h2>
        <div class="admin-thread">{_hilo_html(mensajes)}</div>
        {composer}
    </div>
    <div class="admin-card">{cerrar}</div>"""


@router.get("/{request_id}")
async def detalle(
    request_id: str,
    request: Request,
    financial_admin_session: str | None = Cookie(default=None),
):
    account = await _auth(request, financial_admin_session)
    if not account:
        return _redirect("/admin/login")

    fila = get_expert_request(request_id, account["comuna"])
    if not fila:
        return Response(status_code=404)

    flash = await _pop_flash(request.app.state.redis, financial_admin_session)
    usuario = fila.get("users") or {}
    hito = get_pending_milestone(usuario) if usuario.get("roadmap") else None
    hito_txt = hito["title"] if hito else ("Roadmap completo" if usuario.get("roadmap_completed_at") else "Sin roadmap")
    restante = window_remaining(fila)
    status = fila["status"]
    nombre = account.get("nombre", account["comuna"])
    mensajes = list_expert_messages(request_id)

    if status == "pending":
        cuerpo = _bloque_tomar(request_id, nombre, restante)
    elif status == "in_progress":
        cuerpo = _bloque_conversacion(request_id, mensajes, restante, nombre)
    else:
        cierre = f"{_fecha_local(fila.get('resolved_at'))} por {fila.get('closed_by') or '—'}"
        cuerpo = f"""
    <div class="admin-card">
        <h2>Conversación</h2>
        <p class="subtitulo">Resuelta el {html.escape(cierre)}.</p>
        <div class="admin-thread">{_hilo_html(mensajes)}</div>
    </div>"""

    ventana = (
        f"{_ventana_html(restante)}<small>se renueva cuando el emprendedor escribe · "
        f'<a href="/admin/solicitudes/{html.escape(request_id)}">Actualizar</a></small>'
        if status != "resolved" else "—"
    )
    contenido = f"""
    {_flash_html(flash)}
    <div class="admin-card">
        <div class="admin-req-head">
            <h2>{html.escape(_alias(fila))}</h2>
            {_badge(status)}
        </div>
        <dl class="admin-facts">
            <div><dt>Rubro</dt><dd>{html.escape(usuario.get("rubro") or "—")}</dd></div>
            <div><dt>Hito actual</dt><dd>{html.escape(hito_txt)}</dd></div>
            <div><dt>Solicitada</dt><dd>{html.escape(_fecha_local(fila.get("created_at")))}<small>{html.escape(_hace(fila.get("created_at")))}</small></dd></div>
            <div><dt>Ventana WhatsApp</dt><dd>{ventana}</dd></div>
        </dl>
    </div>
    {cuerpo}
    <p><a href="/admin/solicitudes">← Volver a las solicitudes</a></p>"""
    return Response(content=_pagina_base("Solicitud de experto", contenido, account), media_type="text/html")


async def _notificar(phone: str, texto: str) -> bool:
    try:
        await send_text(phone, texto)
        return True
    except WhatsAppAPIError as error:
        logger.error("No se pudo enviar el mensaje al emprendedor: %s", error)
        return False


@router.post("/{request_id}/tomar")
async def tomar(request_id: str, request: Request, financial_admin_session: str | None = Cookie(default=None)):
    account = await _auth(request, financial_admin_session)
    if not account:
        return _redirect("/admin/login")
    redis = request.app.state.redis
    destino = f"/admin/solicitudes/{request_id}"

    fila = get_expert_request(request_id, account["comuna"])
    if not fila:
        return Response(status_code=404)
    if fila["status"] != "pending":
        await _set_flash(redis, financial_admin_session, "warn", "Esa solicitud ya no está pendiente.")
        return _redirect(destino)
    if window_remaining(fila) <= timedelta(0):
        await _set_flash(
            redis, financial_admin_session, "error",
            "La ventana de 24 h de WhatsApp ya se cerró: no se puede escribirle hasta que el emprendedor vuelva a escribir.",
        )
        return _redirect(destino)

    if not take_expert_request(request_id, account["comuna"], account.get("nombre", account["comuna"])):
        await _set_flash(redis, financial_admin_session, "warn", "Otra persona tomó esa solicitud antes.")
        return _redirect(destino)

    enviado = await _notificar(
        (fila.get("users") or {}).get("phone", ""),
        f"👤 Un experto de *{account.get('nombre', account['comuna'])}* tomó tu solicitud y te "
        "escribirá por este chat.\n\nMientras conversan, el asistente queda en pausa. "
        "Escribe *terminar* cuando quieras volver a hablar con él.",
    )
    if enviado:
        await _set_flash(redis, financial_admin_session, "ok", "Tomaste la solicitud. Ya puedes escribirle.")
    else:
        await _set_flash(redis, financial_admin_session, "error", "La solicitud quedó en atención, pero no se pudo avisar al emprendedor por WhatsApp.")
    return _redirect(destino)


@router.post("/{request_id}/mensaje")
async def enviar_mensaje(
    request_id: str,
    request: Request,
    mensaje: str = Form(...),
    financial_admin_session: str | None = Cookie(default=None),
):
    account = await _auth(request, financial_admin_session)
    if not account:
        return _redirect("/admin/login")
    redis = request.app.state.redis
    destino = f"/admin/solicitudes/{request_id}"

    fila = get_expert_request(request_id, account["comuna"])
    if not fila:
        return Response(status_code=404)

    texto = mensaje.strip()
    if fila["status"] != "in_progress":
        await _set_flash(redis, financial_admin_session, "error", "Solo puedes escribir en solicitudes en atención.")
    elif not texto or len(texto) > MAX_MESSAGE_CHARS:
        await _set_flash(redis, financial_admin_session, "error", f"El mensaje debe tener entre 1 y {MAX_MESSAGE_CHARS} caracteres.")
    elif window_remaining(fila) <= timedelta(0):
        await _set_flash(redis, financial_admin_session, "error", "La ventana de 24 h de WhatsApp está cerrada.")
    elif await _notificar(
        (fila.get("users") or {}).get("phone", ""),
        f"👤 *{account.get('nombre', account['comuna'])}*: {texto}",
    ):
        add_expert_message(request_id, "expert", texto)
        await _set_flash(redis, financial_admin_session, "ok", "Mensaje enviado.")
    else:
        await _set_flash(redis, financial_admin_session, "error", "No se pudo enviar el mensaje por WhatsApp.")
    return _redirect(destino)


@router.post("/{request_id}/resolver")
async def resolver(request_id: str, request: Request, financial_admin_session: str | None = Cookie(default=None)):
    account = await _auth(request, financial_admin_session)
    if not account:
        return _redirect("/admin/login")
    redis = request.app.state.redis

    fila = get_expert_request(request_id, account["comuna"])
    if not fila:
        return Response(status_code=404)

    if resolve_expert_request(request_id, account.get("nombre", account["comuna"]), account["comuna"]):
        await _notificar(
            (fila.get("users") or {}).get("phone", ""),
            "✅ El experto cerró la conversación. Vuelves a hablar con el asistente. "
            "Si necesitas al experto otra vez, puedes pedirlo desde el menú.",
        )
        await _set_flash(redis, financial_admin_session, "ok", "Solicitud marcada como resuelta.")
    else:
        await _set_flash(redis, financial_admin_session, "warn", "La solicitud ya estaba cerrada.")
    return _redirect(f"/admin/solicitudes/{request_id}")
