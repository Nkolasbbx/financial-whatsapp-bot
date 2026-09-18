"""
FinancIAl — routers/admin.py

Dashboard municipal (InnovaRecoleta / El Bosque). Login fijo con 2
cuentas (ver ADMIN_ACCOUNTS en config.py) — primer paso para validar el
concepto con el cliente pagador, sin construir un sistema de usuarios
todavía.

Muestra, para la comuna de la cuenta logueada: cuántos emprendedores
activos hay, cuántos completaron la formalización, en qué hito están
los que siguen en curso, la distribución por rubro, y los insights del
asistente (respuestas no útiles, temas consultados y conflictivos).
"""
import html
import json
import logging
from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Cookie, File, Form, Request, Response, UploadFile

from core.onboarding import RUBRO_DISPLAY, RUBROS_ACTIVOS
from core.roadmaps import get_pending_milestone
from services.admin_insights import get_admin_insights
from db.documents import delete_ingested_document, get_ingestion_job, list_recent_jobs
from db.users import get_users_by_comuna
from services.admin_auth import (
    authenticate_admin,
    create_admin_session,
    destroy_admin_session,
    get_admin_session_account,
)
from services.ingestion_jobs import enqueue_document_ingestion, validate_upload

logger = logging.getLogger("financial")

router = APIRouter(prefix="/admin")

_SESSION_COOKIE = "financial_admin_session"


def _pagina_base(titulo: str, contenido: str, cuenta: dict | None = None) -> str:
    cuenta_html = ""
    if cuenta:
        cuenta_html = f"""
        <div class="admin-account">
            <div>{html.escape(cuenta.get("nombre", ""))}</div>
            <a href="/admin">Panel</a>
            <a href="/admin/documentos">Documentos</a>
            <a href="/admin/logout">Cerrar sesión</a>
        </div>
        """

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(titulo)} · FinancIAl</title>
<link rel="stylesheet" href="/static/admin.css">
</head>
<body>
<div class="admin-container">
    <div class="admin-header">
        <div>
            <div class="admin-eyebrow">FinancIAl · Panel municipal</div>
            <h1>{html.escape(titulo)}</h1>
        </div>
        {cuenta_html}
    </div>
{contenido}
</div>
</body>
</html>"""


def _pagina_login(error: str | None = None) -> str:
    aviso = f'<p class="admin-error">{html.escape(error)}</p>' if error else ""
    contenido = f"""
    <div class="admin-card">
        <h2>Iniciar sesión</h2>
        <form class="admin-login-form" method="post" action="/admin/login">
            <input type="text" name="username" placeholder="Usuario" required autofocus>
            <input type="password" name="password" placeholder="Contraseña" required>
            {aviso}
            <button type="submit">Entrar</button>
        </form>
    </div>
    """
    return _pagina_base("Acceso municipal", contenido)


@router.get("/login")
async def login_form():
    return Response(content=_pagina_login(), media_type="text/html")


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    account = authenticate_admin(username, password)
    if not account:
        return Response(
            content=_pagina_login("Usuario o contraseña incorrectos"),
            media_type="text/html",
            status_code=401,
        )

    redis = request.app.state.redis
    session_id = await create_admin_session(redis, username.strip().lower())

    response = Response(status_code=303, headers={"Location": "/admin"})
    response.set_cookie(
        _SESSION_COOKIE,
        session_id,
        max_age=24 * 3600,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/logout")
async def logout(
    request: Request,
    financial_admin_session: str | None = Cookie(default=None),
):
    redis = request.app.state.redis
    await destroy_admin_session(redis, financial_admin_session)

    response = Response(status_code=303, headers={"Location": "/admin/login"})
    response.delete_cookie(_SESSION_COOKIE)
    return response


@router.get("")
async def dashboard(
    request: Request,
    financial_admin_session: str | None = Cookie(default=None),
):
    redis = request.app.state.redis
    account = await get_admin_session_account(redis, financial_admin_session)

    if not account:
        return Response(status_code=303, headers={"Location": "/admin/login"})

    comuna = account["comuna"]
    usuarios = get_users_by_comuna(comuna)

    total = len(usuarios)
    completados = sum(1 for u in usuarios if u.get("roadmap_completed_at"))
    en_progreso = total - completados

    # --- Distribución por rubro (CA2) ---
    conteo_rubros = Counter(u.get("rubro") or "No definido" for u in usuarios)
    rubros_ordenados = conteo_rubros.most_common()
    max_rubro = rubros_ordenados[0][1] if rubros_ordenados else 1

    filas_rubros = "\n".join(
        f'''<div class="admin-rubro-row">
            <span>{html.escape(nombre)}</span>
            <div class="admin-rubro-bar-track"><div class="admin-rubro-bar-fill" style="width:{max(6, round(cuenta / max_rubro * 100))}%"></div></div>
            <span class="cuenta">{cuenta}</span>
        </div>'''
        for nombre, cuenta in rubros_ordenados
    ) or '<p class="subtitulo">No hay emprendedores registrados todavía.</p>'

    # --- En qué hito están (en progreso) ---
    conteo_hitos: dict[str, int] = {}
    for u in usuarios:
        if u.get("roadmap_completed_at"):
            continue
        hito = get_pending_milestone(u)
        titulo = hito["title"] if hito else "Sin roadmap asignado"
        conteo_hitos[titulo] = conteo_hitos.get(titulo, 0) + 1

    filas_hitos = "\n".join(
        f'<div class="admin-milestone-row"><span>{html.escape(titulo)}</span>'
        f'<span class="admin-milestone-count">{cuenta}</span></div>'
        for titulo, cuenta in sorted(conteo_hitos.items(), key=lambda kv: -kv[1])
    ) or '<p class="subtitulo">No hay emprendedores en progreso todavía.</p>'

    # --- Tabla de emprendedores ---
    filas_emprendedores = []
    for u in usuarios:
        hito = get_pending_milestone(u)
        completo = bool(u.get("roadmap_completed_at"))
        numero_hito = "✔" if completo else (str(hito["id"]) + "/" + str(len(u.get("roadmap"))) if hito else "0")
        estado = "Formalizado" if completo else "En progreso"
        estado_clase = "completo" if completo else "progreso"
        hito_titulo = "Formalización completa" if completo else (hito["title"] if hito else "Sin roadmap asignado")
        rubro = u.get("rubro") or "No definido"

        filas_emprendedores.append(
            f"""<tr>
                <td>{html.escape(u.get("phone", ""))}</td>
                <td><span class="admin-badge {estado_clase}">{estado}</span></td>
                <td>{html.escape("(" + numero_hito + ") - " + hito_titulo)}</td>
                <td>{html.escape(rubro)}</td>
            </tr>"""
        )

    insights = get_admin_insights(comuna)

    contenido = f"""
    <div class="admin-card">
        <h2>Resumen</h2>
        <div class="admin-stats">
            <div class="admin-stat">
                <div class="admin-stat-number">{total}</div>
                <div class="admin-stat-label">Emprendedores activos</div>
            </div>
            <div class="admin-stat">
                <div class="admin-stat-number success">{completados}</div>
                <div class="admin-stat-label">Formalización completa</div>
            </div>
            <div class="admin-stat">
                <div class="admin-stat-number warning">{en_progreso}</div>
                <div class="admin-stat-label">En progreso</div>
            </div>
        </div>
    </div>

    <div class="admin-card">
        <h2>Distribución por rubro</h2>
        <p class="subtitulo">De los {total} emprendedores activos en la comuna.</p>
        {filas_rubros}
    </div>

    <div class="admin-card">
        <h2>En qué hito están</h2>
        <p class="subtitulo">Los {en_progreso} emprendedores que aún no completan la formalización.</p>
        {filas_hitos}
    </div>

    <div class="admin-card">
        <h2>Emprendedores de {html.escape(comuna)}</h2>
        <table class="admin-table">
            <thead>
                <tr><th>Teléfono</th><th>Estado</th><th>Hito</th><th>Rubro</th></tr>
            </thead>
            <tbody>
                {''.join(filas_emprendedores) or '<tr><td colspan="4">No hay emprendedores</td></tr>'}
            </tbody>
        </table>
    </div>

    <div class="admin-card">
        <h2>Insights del asistente</h2>
        <p class="subtitulo">Respuestas no útiles reportadas: {insights["no_useful"]}</p>
        <div class="admin-insights-columns">
            <div>
                <h3>Temas consultados</h3>
                <ul>
                    {''.join(f'<li><span>{html.escape(k)}</span><span>{v}</span></li>' for k, v in insights['topics'].items()) or '<li>Sin datos</li>'}
                </ul>
            </div>
            <div>
                <h3>Temas más conflictivos</h3>
                <ul>
                    {''.join(f'<li><span>{html.escape(k)}</span><span>{v}</span></li>' for k, v in insights['conflictivos'].items()) or '<li>No hay reportes</li>'}
                </ul>
            </div>
        </div>
    </div>

    <div class="admin-logout"><a href="/admin/logout">Cerrar sesión</a></div>
    """

    return Response(
        content=_pagina_base(account.get("nombre", comuna), contenido, account),
        media_type="text/html",
    )


# --- HdU15: ingesta automática de documentos ---

_ESTADO_LABELS = {
    "queued": ("En cola", "progreso"),
    "processing": ("Procesando", "progreso"),
    "done": ("Listo", "completo"),
    "failed": ("Error", "progreso"),
}


def _fila_ingestion_job(job: dict) -> str:
    label, badge_clase = _ESTADO_LABELS.get(job["status"], (job["status"], "progreso"))
    rubros = ", ".join(RUBRO_DISPLAY.get(r, r) for r in (job.get("rubros") or [])) or "—"
    vigencia = job.get("vigencia_hasta")
    vigencia_txt = f"hasta {vigencia}" if vigencia else "sin vencimiento"
    detalle = ""
    acciones = "—"

    if job.get("deleted_at"):
        label, badge_clase = "Eliminado", "eliminado"
        fecha = str(job["deleted_at"])[:16].replace("T", " ")
        detalle = f'<div class="subtitulo">Eliminado por {html.escape(job.get("deleted_by") or "—")} el {html.escape(fecha)}</div>'
    elif job["status"] == "failed" and job.get("error_message"):
        detalle = f'<div class="subtitulo">{html.escape(job["error_message"][:200])}</div>'
    elif job.get("review_flag"):
        detalle = '<div class="subtitulo">⚠️ Revisar: puede haber contenido no legible (tablas/imágenes).</div>'

    if job["status"] == "done" and not job.get("deleted_at"):
        acciones = f'''<form method="post" action="/admin/documentos/{job["id"]}/eliminar"
                onsubmit="return confirm('¿Eliminar este documento? El asistente dejará de citarlo.')">
            <button type="submit" class="admin-delete-button">Eliminar</button>
        </form>'''

    return f"""<tr>
        <td>{html.escape(job["file_name"])}{detalle}</td>
        <td>{html.escape(rubros)}</td>
        <td>{html.escape(vigencia_txt)}</td>
        <td><span class="admin-badge {badge_clase}">{html.escape(label)}</span></td>
        <td>{job.get("chunks_written") if job.get("chunks_written") is not None else "—"}</td>
        <td>{acciones}</td>
    </tr>"""


def _pagina_documentos(account: dict, error: str | None = None) -> str:
    checkboxes = "\n".join(
        f'''<label class="admin-checkbox">
            <input type="checkbox" name="rubros" value="{html.escape(rubro)}"> {html.escape(RUBRO_DISPLAY.get(rubro, rubro))}
        </label>'''
        for rubro in RUBROS_ACTIVOS
    )
    jobs = list_recent_jobs(account["comuna"], limit=20)
    filas = "\n".join(_fila_ingestion_job(job) for job in jobs) or (
        '<tr><td colspan="6">Todavía no se han subido documentos.</td></tr>'
    )
    aviso = f'<p class="admin-error">{html.escape(error)}</p>' if error else ""

    contenido = f"""
    <div class="admin-card">
        <h2>Subir documento</h2>
        <p class="subtitulo">
            Se etiqueta automáticamente con la comuna de tu cuenta
            ({html.escape(account["comuna"])}). El procesamiento ocurre en
            segundo plano: la nueva información queda disponible para el
            asistente en unos minutos.
        </p>
        {aviso}
        <form class="admin-upload-form" method="post" action="/admin/documentos/subir" enctype="multipart/form-data">
            <label>
                Archivo (PDF o Markdown)
                <input type="file" name="archivo" accept=".pdf,.md" required>
            </label>
            <fieldset>
                <legend>Rubros aplicables</legend>
                <label class="admin-checkbox">
                    <input type="checkbox" name="rubros" value="general" checked> General (todos los rubros)
                </label>
                {checkboxes}
            </fieldset>
            <label>
                Vigente desde (opcional)
                <input type="date" name="vigencia_desde">
            </label>
            <label>
                Vigente hasta (opcional — sin fecha significa que no vence)
                <input type="date" name="vigencia_hasta">
            </label>
            <button type="submit">Subir e iniciar ingesta</button>
        </form>
    </div>

    <div class="admin-card">
        <h2>Últimas subidas de {html.escape(account["comuna"])}</h2>
        <table class="admin-table">
            <thead>
                <tr><th>Documento</th><th>Rubros</th><th>Vigencia</th><th>Estado</th><th>Chunks</th><th>Acciones</th></tr>
            </thead>
            <tbody>
                {filas}
            </tbody>
        </table>
    </div>
    """
    return _pagina_base("Documentos", contenido, account)


@router.get("/documentos")
async def documentos_form(
    request: Request,
    financial_admin_session: str | None = Cookie(default=None),
):
    redis = request.app.state.redis
    account = await get_admin_session_account(redis, financial_admin_session)
    if not account:
        return Response(status_code=303, headers={"Location": "/admin/login"})

    return Response(content=_pagina_documentos(account), media_type="text/html")


def _parse_vigencia(valor: str | None) -> str | None:
    """Valida que una fecha de vigencia venga en formato yyyy-mm-dd (o vacía)."""
    if not valor or not valor.strip():
        return None
    datetime.strptime(valor.strip(), "%Y-%m-%d")  # lanza ValueError si es inválida
    return valor.strip()


@router.post("/documentos/subir")
async def subir_documento(
    request: Request,
    financial_admin_session: str | None = Cookie(default=None),
    archivo: UploadFile = File(...),
    rubros: list[str] = Form(default_factory=list),
    vigencia_desde: str | None = Form(default=None),
    vigencia_hasta: str | None = Form(default=None),
):
    redis = request.app.state.redis
    account = await get_admin_session_account(redis, financial_admin_session)
    if not account:
        return Response(status_code=303, headers={"Location": "/admin/login"})

    raw_bytes = await archivo.read()

    error = validate_upload(archivo.filename or "", archivo.content_type, len(raw_bytes))
    if error is None:
        try:
            vigencia_desde = _parse_vigencia(vigencia_desde)
            vigencia_hasta = _parse_vigencia(vigencia_hasta)
        except ValueError:
            error = "Las fechas de vigencia deben tener el formato AAAA-MM-DD."

    if error:
        return Response(
            content=_pagina_documentos(account, error=error),
            media_type="text/html",
            status_code=400,
        )

    job_id = await enqueue_document_ingestion(
        redis=redis,
        raw_bytes=raw_bytes,
        file_name=archivo.filename,
        content_type=archivo.content_type,
        comuna=account["comuna"],  # nunca desde el form: evita etiquetar documentos de otra comuna
        uploaded_by=account.get("nombre", account["comuna"]),
        rubros=rubros or ["general"],
        vigencia_desde=vigencia_desde,
        vigencia_hasta=vigencia_hasta,
    )

    return Response(
        status_code=202,
        content=json.dumps({"job_id": job_id, "status": "queued"}),
        media_type="application/json",
    )


@router.get("/documentos/estado/{job_id}")
async def estado_ingesta(
    job_id: str,
    request: Request,
    financial_admin_session: str | None = Cookie(default=None),
):
    redis = request.app.state.redis
    account = await get_admin_session_account(redis, financial_admin_session)
    if not account:
        return Response(status_code=401)

    job = get_ingestion_job(job_id)
    if not job or job["comuna"] != account["comuna"]:
        return Response(status_code=404)

    return Response(content=json.dumps(job, default=str), media_type="application/json")


@router.post("/documentos/{job_id}/eliminar")
async def eliminar_documento(
    job_id: str,
    request: Request,
    financial_admin_session: str | None = Cookie(default=None),
):
    """Quita un documento ya ingerido del índice del asistente (borra sus
    filas de `documents`); el registro de auditoría en `ingestion_jobs`
    queda marcado con deleted_at/deleted_by, no se borra."""
    redis = request.app.state.redis
    account = await get_admin_session_account(redis, financial_admin_session)
    if not account:
        return Response(status_code=303, headers={"Location": "/admin/login"})

    job = get_ingestion_job(job_id)
    if not job or job["comuna"] != account["comuna"]:
        return Response(status_code=404)

    if job["status"] != "done" or job.get("deleted_at"):
        return Response(
            content=_pagina_documentos(
                account, error="Solo se pueden eliminar documentos ya procesados y no eliminados previamente."
            ),
            media_type="text/html",
            status_code=400,
        )

    delete_ingested_document(
        job["file_name"], job_id, account.get("nombre", account["comuna"])
    )

    return Response(status_code=303, headers={"Location": "/admin/documentos"})
