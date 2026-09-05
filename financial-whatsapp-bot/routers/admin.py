"""
FinancIAl — routers/admin.py

Dashboard municipal (InnovaRecoleta / El Bosque). Login fijo con 2
cuentas (ver ADMIN_ACCOUNTS en config.py) — primer paso para validar el
concepto con el cliente pagador, sin construir un sistema de usuarios
todavía.

Muestra, para la comuna de la cuenta logueada: cuántos emprendedores
activos hay, cuántos completaron la formalización, y en qué hito están
los que siguen en curso.
"""
import html
import logging

from fastapi import APIRouter, Cookie, Form, Request, Response

from core.roadmaps import get_pending_milestone
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


def _pagina_base(titulo: str, contenido: str) -> str:
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titulo} · FinancIAl</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
    * {{ box-sizing: border-box; }}
    body {{
        font-family: "Inter", -apple-system, "Segoe UI", Roboto, sans-serif;
        background: #eef2f6;
        margin: 0;
        color: #1a1a1a;
    }}
    .encabezado {{
        background: linear-gradient(120deg, #4338ca 0%, #2563eb 100%);
        color: white;
        padding: 28px 16px 56px;
        text-align: center;
    }}
    .encabezado .logo {{
        font-size: 15px;
        font-weight: 700;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        opacity: 0.85;
        margin-bottom: 4px;
    }}
    .encabezado h2 {{ margin: 0; font-size: 24px; font-weight: 800; }}
    .contenedor {{
        max-width: 720px;
        margin: -36px auto 0;
        padding: 0 16px 60px;
    }}
    .tarjeta {{
        background: white;
        border-radius: 16px;
        padding: 22px;
        margin-bottom: 18px;
        box-shadow: 0 4px 16px rgba(16, 24, 40, 0.08);
    }}
    h1 {{ font-size: 18px; margin: 0 0 14px; font-weight: 700; }}
    .subtitulo {{ color: #667085; font-size: 14px; }}
    .stats {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        gap: 14px;
    }}
    .stat {{
        background: #f8f9fb;
        border-radius: 12px;
        padding: 16px;
        text-align: center;
    }}
    .stat-numero {{ font-size: 30px; font-weight: 800; color: #2563eb; }}
    .stat-label {{ font-size: 13px; color: #667085; margin-top: 4px; }}
    .hito-fila {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 12px;
        padding: 10px 0;
        border-top: 1px solid #eef0f2;
        font-size: 14px;
    }}
    .hito-fila:first-of-type {{ border-top: none; }}
    .hito-fila .cuenta {{
        background: #eef2ff;
        color: #4338ca;
        font-weight: 700;
        padding: 2px 10px;
        border-radius: 999px;
        font-size: 13px;
        white-space: nowrap;
    }}
    form.login {{ display: flex; flex-direction: column; gap: 12px; }}
    form.login input {{
        padding: 12px 14px;
        border: 1px solid #d0d5dd;
        border-radius: 10px;
        font-size: 15px;
        font-family: inherit;
    }}
    form.login button {{
        background: linear-gradient(120deg, #4338ca, #2563eb);
        color: white;
        border: none;
        padding: 12px;
        border-radius: 10px;
        font-size: 15px;
        font-weight: 700;
        cursor: pointer;
    }}
    .error {{ color: #b42318; font-size: 14px; margin: -4px 0 0; }}
    .cerrar-sesion {{ text-align: center; margin-top: 8px; }}
    .cerrar-sesion a {{ color: #667085; font-size: 13px; text-decoration: none; }}
</style>
</head>
<body>
<div class="encabezado">
    <div class="logo">FinancIAl · Panel municipal</div>
    <h2>{titulo}</h2>
</div>
<div class="contenedor">
{contenido}
</div>
</body>
</html>"""


def _pagina_login(error: str | None = None) -> str:
    aviso = f'<p class="error">{html.escape(error)}</p>' if error else ""
    contenido = f"""
    <div class="tarjeta">
        <h1>Iniciar sesión</h1>
        <form class="login" method="post" action="/admin/login">
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

    conteo_hitos: dict[str, int] = {}
    for u in usuarios:
        if u.get("roadmap_completed_at"):
            continue
        hito = get_pending_milestone(u)
        titulo = hito["title"] if hito else "Sin roadmap asignado"
        conteo_hitos[titulo] = conteo_hitos.get(titulo, 0) + 1

    filas_hitos = "\n".join(
        f'<div class="hito-fila"><span>{html.escape(titulo)}</span>'
        f'<span class="cuenta">{cuenta}</span></div>'
        for titulo, cuenta in sorted(conteo_hitos.items(), key=lambda kv: -kv[1])
    ) or '<p class="subtitulo">No hay emprendedores en progreso todavía.</p>'

    contenido = f"""
    <div class="tarjeta">
        <h1>Resumen — {html.escape(comuna)}</h1>
        <div class="stats">
            <div class="stat">
                <div class="stat-numero">{total}</div>
                <div class="stat-label">Emprendedores activos</div>
            </div>
            <div class="stat">
                <div class="stat-numero">{completados}</div>
                <div class="stat-label">Formalización completa</div>
            </div>
            <div class="stat">
                <div class="stat-numero">{en_progreso}</div>
                <div class="stat-label">En progreso</div>
            </div>
        </div>
    </div>
    <div class="tarjeta">
        <h1>En qué hito están (en progreso)</h1>
        {filas_hitos}
    </div>
    <div class="cerrar-sesion"><a href="/admin/logout">Cerrar sesión</a></div>
    """

    return Response(
        content=_pagina_base(account.get("nombre", comuna), contenido),
        media_type="text/html",
    )
