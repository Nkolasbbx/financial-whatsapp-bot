"""
FinancIAl — routers/admin_solicitudes.py

Solicitudes de contacto con un experto (HdU16), lado del admin municipal.
El admin ve las solicitudes de SU comuna, toma una y conversa con el
emprendedor desde el panel. Los mensajes salen y entran por el número del
bot: el teléfono del emprendedor nunca se renderiza acá, y el emprendedor
nunca ve el del admin.

La lista se ordena por urgencia (primero las que están por cerrar su ventana
de 24 h de WhatsApp) y se puede filtrar por tema, rubro, formalización y
ventana por cerrar.
"""
import html
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Form, Request, Response
from fastapi.responses import JSONResponse

from core.calendar_service import get_calendar_timezone
from core.expert_flow import TOPIC_LABELS, TOPICS
from core.roadmaps import get_pending_milestone
from db.expert_requests import (
    add_expert_message,
    get_expert_request,
    list_expert_messages,
    list_expert_requests,
    parse_timestamp as _parse_dt,
    reopen_expert_request,
    resolve_expert_request,
    take_expert_request,
)
from routers.admin import _flash_html, _opciones, _pagina_base, _pop_flash, _set_flash
from services.admin_auth import get_admin_session_account
from services.whatsapp import WhatsAppAPIError, send_text

logger = logging.getLogger("financial")

router = APIRouter(prefix="/admin/solicitudes")

WHATSAPP_WINDOW = timedelta(hours=24)
URGENT_WINDOW = timedelta(hours=6)
MAX_MESSAGE_CHARS = 1000
REASON_PREVIEW_CHARS = 90

_ESTADOS = {
    "pending": ("Pendiente", "en-cola"),
    "in_progress": ("En atención", "procesando"),
    "resolved": ("Resuelta", "listo"),
}


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


def _formalizado_txt(usuario: dict) -> str:
    estado = usuario.get("inicio_sii")
    if estado == "si":
        return "Formalizado"
    if estado == "no":
        return "No formalizado"
    return "Formalización sin confirmar"


def _progreso(usuario: dict) -> tuple[int, int]:
    roadmap = usuario.get("roadmap") or []
    return sum(1 for hito in roadmap if hito.get("done")), len(roadmap)


def _progreso_txt(usuario: dict) -> str:
    hechos, total = _progreso(usuario)
    return f"{hechos} de {total} hitos" if total else "Sin roadmap"


def ordenar_por_urgencia(filas: list[dict]) -> list[dict]:
    """Primero las que están por cerrar su ventana; las ya cerradas, al final."""
    def clave(fila: dict):
        restante = window_remaining(fila)
        return (restante <= timedelta(0), restante)
    return sorted(filas, key=clave)


def filtrar(filas: list[dict], tema: str = "", rubro: str = "", formalizado: str = "", vence: bool = False) -> list[dict]:
    resultado = []
    for fila in filas:
        usuario = fila.get("users") or {}
        if tema and fila.get("topic") != tema:
            continue
        if rubro and (usuario.get("rubro") or "") != rubro:
            continue
        if formalizado == "si" and usuario.get("inicio_sii") != "si":
            continue
        if formalizado == "no" and usuario.get("inicio_sii") == "si":
            continue
        if vence:
            restante = window_remaining(fila)
            if not (timedelta(0) < restante < URGENT_WINDOW):
                continue
        resultado.append(fila)
    return resultado


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
    elif restante < URGENT_WINDOW:
        clase = "warn"
    else:
        clase = "ok"
    return f'<span class="admin-window-{clase}">{html.escape(_window_text(restante))}</span>'


def _tema_html(fila: dict) -> str:
    tema = TOPIC_LABELS.get(fila.get("topic") or "", "")
    chip = f'<span class="admin-chip">{html.escape(tema)}</span>' if tema else '<span class="admin-wait">Sin tema</span>'
    motivo = (fila.get("reason") or "").strip()
    if len(motivo) > REASON_PREVIEW_CHARS:
        motivo = motivo[:REASON_PREVIEW_CHARS].rstrip() + "…"
    return chip + (f'<div class="admin-wait">{html.escape(motivo)}</div>' if motivo else "")


def _tabla(titulo: str, filas: list[dict], vacio: str) -> str:
    cuerpo = "\n".join(
        f"""<tr>
            <td><strong>{html.escape(_alias(r))}</strong>
                <div class="admin-wait">{html.escape((r.get("users") or {}).get("rubro") or "—")}</div></td>
            <td>{_tema_html(r)}</td>
            <td>{html.escape(_formalizado_txt(r.get("users") or {}))}
                <div class="admin-wait">{html.escape(_progreso_txt(r.get("users") or {}))}</div></td>
            <td>{html.escape(_fecha_local(r.get("created_at")))}<div class="admin-wait">{html.escape(_hace(r.get("created_at")))}</div></td>
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
            <thead><tr><th>Emprendedor</th><th>Tema y motivo</th><th>Contexto</th><th>Solicitada</th><th>Ventana WhatsApp</th><th></th></tr></thead>
            <tbody>{cuerpo}</tbody>
        </table>
    </div>"""


def _filtros_html(rubros: list[str], tema: str, rubro: str, formalizado: str, vence: bool) -> str:
    return f"""
    <form method="get" action="/admin/solicitudes" class="admin-filters" aria-label="Filtrar solicitudes">
        <label>Tema <select name="tema">{_opciones(list(TOPICS), tema, "Todos")}</select></label>
        <label>Rubro <select name="rubro">{_opciones([(r, r.capitalize()) for r in rubros], rubro, "Todos")}</select></label>
        <label>Formalización <select name="formalizado">{_opciones([("si", "Formalizados"), ("no", "No formalizados")], formalizado, "Todas")}</select></label>
        <label class="admin-filter-check"><input type="checkbox" name="vence" value="1"{" checked" if vence else ""}> Ventana por cerrar (menos de 6 h)</label>
        <button type="submit" class="admin-btn-secondary">Filtrar</button>
        <a href="/admin/solicitudes" class="admin-filter-clear">Limpiar</a>
    </form>"""


@router.get("")
async def lista(request: Request, financial_admin_session: str | None = Cookie(default=None)):
    account = await _auth(request, financial_admin_session)
    if not account:
        return _redirect("/admin/login")

    comuna = account["comuna"]
    params = request.query_params
    tema = params.get("tema", "") if params.get("tema", "") in TOPIC_LABELS else ""
    rubro = params.get("rubro", "")
    formalizado = params.get("formalizado", "") if params.get("formalizado", "") in ("si", "no") else ""
    vence = params.get("vence") == "1"

    pendientes = list_expert_requests(comuna, ("pending",))
    en_atencion = list_expert_requests(comuna, ("in_progress",))
    resueltas = list_expert_requests(comuna, ("resolved",), limit=10)
    rubros = sorted({(f.get("users") or {}).get("rubro") for f in pendientes + en_atencion + resueltas if (f.get("users") or {}).get("rubro")})

    def preparar(filas, abiertas=True):
        filas = filtrar(filas, tema, rubro, formalizado, vence and abiertas)
        return ordenar_por_urgencia(filas) if abiertas else filas

    flash = await _pop_flash(request.app.state.redis, financial_admin_session)
    contenido = (
        _flash_html(flash)
        + _filtros_html(rubros, tema, rubro, formalizado, vence)
        + _tabla("Pendientes", preparar(pendientes), "No hay solicitudes pendientes con esos filtros.")
        + _tabla("En atención", preparar(en_atencion), "No hay conversaciones en curso con esos filtros.")
        + _tabla("Resueltas (recientes)", preparar(resueltas, abiertas=False), "Todavía no hay solicitudes resueltas.")
    )
    return Response(content=_pagina_base("Solicitudes de experto", contenido, account), media_type="text/html")


QUOTE_PREVIEW_CHARS = 120


def _extracto(texto: str) -> str:
    texto = " ".join((texto or "").split())
    return texto if len(texto) <= QUOTE_PREVIEW_CHARS else texto[:QUOTE_PREVIEW_CHARS].rstrip() + "…"


def _firma(mensajes: list[dict]) -> str:
    """Cambia cuando llega o se envía un mensaje: el navegador la compara para saber si refrescar."""
    return f"{len(mensajes)}:{mensajes[-1].get('id') if mensajes else 0}"


def _mensaje_citable(mensajes: list[dict], mensaje_id) -> dict | None:
    """Un mensaje del emprendedor que se puede citar (tiene el wamid guardado)."""
    try:
        mensaje_id = int(mensaje_id)
    except (TypeError, ValueError):
        return None
    return next(
        (m for m in mensajes if m.get("id") == mensaje_id and m["sender"] == "user" and m.get("wa_message_id")),
        None,
    )


def _hilo_html(
    mensajes: list[dict],
    request_id: str | None = None,
    puede_responder: bool = False,
    seleccionado: int | None = None,
) -> str:
    if not mensajes:
        return '<p class="admin-thread-empty">Todavía no hay mensajes en esta conversación.</p>'
    por_id = {m.get("id"): m for m in mensajes}
    burbujas = []
    for m in mensajes:
        es_experto = m["sender"] == "expert"
        cita = ""
        citado = por_id.get(m.get("reply_to_id")) if es_experto else None
        if citado:
            cita = f'<div class="admin-quote">↩ {html.escape(_extracto(citado["content"]))}</div>'
        responder = ""
        if puede_responder and request_id and not es_experto and m.get("wa_message_id") and m.get("id"):
            marca = " admin-msg-selected" if m["id"] == seleccionado else ""
            responder = (
                f'<a class="admin-reply-link" href="/admin/solicitudes/{html.escape(request_id)}'
                f'?responder={int(m["id"])}#mensaje">Responder</a>'
            )
        else:
            marca = ""
        burbujas.append(
            f'<div class="admin-msg admin-msg-{"expert" if es_experto else "user"}{marca}">'
            f'<span class="admin-msg-meta">{"Tú" if es_experto else "Emprendedor"} · {html.escape(_fecha_local(m.get("created_at")))}</span>'
            f'{cita}<p>{html.escape(m["content"])}</p>{responder}</div>'
        )
    return "\n".join(burbujas)


def _bloque_motivo(fila: dict) -> str:
    motivo = (fila.get("reason") or "").strip()
    texto = html.escape(motivo) if motivo else "El emprendedor no dejó detalle."
    return f"""
    <div class="admin-card">
        <h2>Motivo de la solicitud</h2>
        <blockquote class="admin-reason">{texto}</blockquote>
    </div>"""


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


def _bloque_conversacion(
    request_id: str,
    mensajes: list[dict],
    restante: timedelta,
    nombre: str,
    responder: dict | None = None,
) -> str:
    if restante > timedelta(0):
        cita = ""
        if responder:
            cita = f"""
            <div class="admin-quote-preview">
                <span>Respondiendo a: «{html.escape(_extracto(responder["content"]))}»</span>
                <a href="/admin/solicitudes/{html.escape(request_id)}#mensaje">Quitar cita</a>
            </div>
            <input type="hidden" name="responder_a" value="{int(responder["id"])}">"""
        composer = f"""
        <form method="post" action="/admin/solicitudes/{html.escape(request_id)}/mensaje" class="admin-compose">
            <label for="mensaje">Escribe tu respuesta</label>{cita}
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
    hilo = _hilo_html(
        mensajes, request_id, puede_responder=restante > timedelta(0),
        seleccionado=responder["id"] if responder else None,
    )
    return f"""
    <div class="admin-card">
        <h2>Conversación</h2>
        <div id="nuevo-mensaje" class="admin-new-msg" role="status" aria-live="polite" hidden></div>
        <div class="admin-thread" id="hilo">{hilo}</div>
        {composer}
    </div>
    <div class="admin-card">{cerrar}</div>"""


@router.get("/{request_id}/hilo")
async def hilo(
    request_id: str,
    request: Request,
    financial_admin_session: str | None = Cookie(default=None),
):
    """Estado y conversación en JSON: la página lo consulta cada pocos segundos
    para mostrar mensajes nuevos sin recargar (y sin borrar lo que se escribe)."""
    account = await _auth(request, financial_admin_session)
    if not account:
        return JSONResponse({"error": "sesion"}, status_code=401)

    fila = get_expert_request(request_id, account["comuna"])
    if not fila:
        return JSONResponse({"error": "no encontrada"}, status_code=404)

    mensajes = list_expert_messages(request_id)
    restante = window_remaining(fila)
    status = fila["status"]
    citado = _mensaje_citable(mensajes, request.query_params.get("responder"))
    return JSONResponse({
        "status": status,
        "sig": _firma(mensajes),
        "hilo_html": _hilo_html(
            mensajes, request_id, puede_responder=restante > timedelta(0),
            seleccionado=citado["id"] if citado else None,
        ),
        "ventana_html": _ventana_html(restante) if status != "resolved" else "—",
    })


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
        responder = _mensaje_citable(mensajes, request.query_params.get("responder"))
        cuerpo = _bloque_conversacion(request_id, mensajes, restante, nombre, responder)
    else:
        cerrada_por = fila.get("closed_by") or "—"
        cierre = f"{_fecha_local(fila.get('resolved_at'))} por {cerrada_por}"
        aviso = (
            '<p class="admin-hint">El emprendedor terminó la conversación. Puedes dejarla cerrada o volver a dejarla pendiente.</p>'
            if str(cerrada_por).startswith("emprendedor") else ""
        )
        cuerpo = f"""
    <div class="admin-card">
        <h2>Conversación</h2>
        <p class="subtitulo">Resuelta el {html.escape(cierre)}.</p>
        <div class="admin-thread">{_hilo_html(mensajes)}</div>
        {aviso}
        <form method="post" action="/admin/solicitudes/{html.escape(request_id)}/reabrir"
              onsubmit="return confirm('¿Dejar esta solicitud pendiente otra vez?')">
            <div class="admin-form-actions">
                <span class="admin-hint">Queda cerrada si no haces nada.</span>
                <button type="submit" class="admin-btn-secondary">Dejar pendiente</button>
            </div>
        </form>
    </div>"""

    ventana = (
        f'<span id="ventana-valor">{_ventana_html(restante)}</span><small>se renueva cuando el emprendedor escribe · '
        f'<a href="/admin/solicitudes/{html.escape(request_id)}">Actualizar</a></small>'
        if status != "resolved" else "—"
    )
    tema = TOPIC_LABELS.get(fila.get("topic") or "", "Sin tema")
    vigia = (
        f'<div id="hilo-watch" hidden data-request="{html.escape(request_id)}" '
        f'data-status="{html.escape(status)}" data-sig="{html.escape(_firma(mensajes))}"></div>'
        '<script src="/static/admin_solicitudes.js"></script>'
        if status in ("pending", "in_progress") else ""
    )
    contenido = f"""
    {_flash_html(flash)}
    <div class="admin-card">
        <div class="admin-req-head">
            <h2>{html.escape(_alias(fila))}</h2>
            {_badge(status)}
        </div>
        <dl class="admin-facts">
            <div><dt>Tema</dt><dd>{html.escape(tema)}</dd></div>
            <div><dt>Rubro</dt><dd>{html.escape(usuario.get("rubro") or "—")}</dd></div>
            <div><dt>Formalización</dt><dd>{html.escape(_formalizado_txt(usuario))}</dd></div>
            <div><dt>Roadmap</dt><dd>{html.escape(_progreso_txt(usuario))}<small>Hito actual: {html.escape(hito_txt)}</small></dd></div>
            <div><dt>Solicitada</dt><dd>{html.escape(_fecha_local(fila.get("created_at")))}<small>{html.escape(_hace(fila.get("created_at")))}</small></dd></div>
            <div><dt>Ventana WhatsApp</dt><dd>{ventana}</dd></div>
        </dl>
    </div>
    {_bloque_motivo(fila)}
    {cuerpo}
    {vigia}
    <p><a href="/admin/solicitudes">← Volver a las solicitudes</a></p>"""
    return Response(content=_pagina_base("Solicitud de experto", contenido, account), media_type="text/html")


async def _notificar(phone: str, texto: str, reply_to: str | None = None) -> bool:
    try:
        await send_text(phone, texto, reply_to_message_id=reply_to) if reply_to else await send_text(phone, texto)
        return True
    except WhatsAppAPIError as error:
        if reply_to:
            # Si WhatsApp rechaza la cita (por ejemplo un id vencido), el
            # mensaje igual se manda, solo que sin citar.
            logger.warning("No se pudo citar el mensaje, se envía sin cita: %s", error)
            return await _notificar(phone, texto)
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
    responder_a: str = Form(""),
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

    # Solo se puede citar un mensaje del emprendedor de ESTA conversación.
    citado = None
    if isinstance(responder_a, str) and responder_a.strip():
        citado = _mensaje_citable(list_expert_messages(request_id), responder_a)

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
        reply_to=citado["wa_message_id"] if citado else None,
    ):
        add_expert_message(request_id, "expert", texto, reply_to_id=citado["id"] if citado else None)
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


@router.post("/{request_id}/reabrir")
async def reabrir(request_id: str, request: Request, financial_admin_session: str | None = Cookie(default=None)):
    account = await _auth(request, financial_admin_session)
    if not account:
        return _redirect("/admin/login")
    redis = request.app.state.redis

    fila = get_expert_request(request_id, account["comuna"])
    if not fila:
        return Response(status_code=404)

    try:
        reabierta = reopen_expert_request(request_id, account["comuna"])
    except Exception as error:
        # Índice único: el emprendedor ya tiene otra solicitud abierta.
        logger.warning("No se pudo reabrir la solicitud %s: %s", request_id, error)
        await _set_flash(redis, financial_admin_session, "error", "No se pudo dejar pendiente: el emprendedor ya tiene otra solicitud abierta.")
        return _redirect(f"/admin/solicitudes/{request_id}")

    if reabierta:
        await _set_flash(redis, financial_admin_session, "ok", "La solicitud quedó pendiente otra vez.")
    else:
        await _set_flash(redis, financial_admin_session, "warn", "La solicitud no estaba cerrada.")
    return _redirect(f"/admin/solicitudes/{request_id}")
