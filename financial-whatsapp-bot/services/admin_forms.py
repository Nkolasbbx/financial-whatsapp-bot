"""Protección de los formularios HTML del panel municipal."""
from html import escape

from fastapi import HTTPException, Request
from starlette.datastructures import FormData

from services.admin_auth import validate_admin_csrf_token


def csrf_input(token: str) -> str:
    return f'<input type="hidden" name="csrf_token" value="{escape(token, quote=True)}">'


async def require_form_csrf(request: Request, session_id: str, form: FormData | None = None) -> None:
    if form is None:
        async with request.form() as submitted:
            await require_form_csrf(request, session_id, submitted)
        return
    tokens = form.getlist("csrf_token")
    if (len(tokens) != 1 or not isinstance(tokens[0], str) or not tokens[0].isascii()
            or not await validate_admin_csrf_token(request.app.state.redis, session_id, tokens[0])):
        raise HTTPException(403, "La solicitud no es válida. Recarga la página y vuelve a intentarlo.")
