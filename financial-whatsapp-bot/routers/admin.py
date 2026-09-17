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
import logging
from collections import Counter

from fastapi import APIRouter, Cookie, Form, Request, Response

from core.roadmaps import get_pending_milestone
from services.admin_insights import get_admin_insights
from db.users import get_users_by_comuna
from services.admin_auth import (
    authenticate_admin,
    create_admin_session,
    destroy_admin_session,
    get_admin_session_account,
)

logger = logging.getLogger("financial")

router = APIRouter(prefix="/admin")

_SESSION_COOKIE = "financial_admin_session"


def _pagina_base(titulo: str, contenido: str, cuenta: dict | None = None) -> str:
    cuenta_html = ""
    if cuenta:
        cuenta_html = f"""
        <div class="admin-account">
            <div>{html.escape(cuenta.get("nombre", ""))}</div>
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