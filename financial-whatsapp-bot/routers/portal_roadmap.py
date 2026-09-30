"""API privada del roadmap de formalización del panel del emprendedor.

Reutiliza las mismas reglas del bot (``core.roadmaps``): los hitos avanzan
en orden, solo el pendiente actual puede marcarse como listo y al completar
el último el perfil pasa a formalizado.
"""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from core.roadmaps import (
    get_last_completed_milestone,
    get_pending_milestone,
    mark_hito_done,
    revert_last_hito,
)
from db.users import get_user, save_user
from services.portal_auth import get_session_phone, validate_csrf_token


router = APIRouter(prefix="/portal/api/roadmap", tags=["portal-roadmap"])

_SESSION_COOKIE = "financial_session"


async def _authenticated_user(
    request: Request,
    session_id: str | None,
) -> dict:
    redis = request.app.state.redis
    if redis is None:
        raise HTTPException(
            status_code=503,
            detail="El acceso al panel no está disponible temporalmente",
        )

    phone = await get_session_phone(redis, session_id)
    if not phone:
        raise HTTPException(status_code=401, detail="La sesión venció")

    user = await run_in_threadpool(get_user, phone)
    if not user:
        raise HTTPException(status_code=404, detail="No encontramos el perfil")
    return user


async def _require_csrf(
    request: Request,
    session_id: str | None,
    submitted_token: str | None,
) -> None:
    redis = request.app.state.redis
    if redis is None or not await validate_csrf_token(
        redis,
        session_id,
        submitted_token,
    ):
        raise HTTPException(
            status_code=403,
            detail="La solicitud del roadmap no es válida",
        )


def _progress(user: dict) -> dict:
    roadmap = user.get("roadmap") or []
    completed = sum(1 for milestone in roadmap if milestone.get("done"))
    total = len(roadmap)
    return {
        "completed": completed,
        "total": total,
        "percentage": round((completed / total) * 100) if total else 0,
        "formalized": user.get("inicio_sii") == "si",
    }


@router.post("/milestones/{milestone_id}/done")
async def complete_milestone(
    milestone_id: int,
    request: Request,
    financial_session: str | None = Cookie(
        default=None,
        alias=_SESSION_COOKIE,
    ),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    user = await _authenticated_user(request, financial_session)
    await _require_csrf(request, financial_session, csrf_token)

    if user.get("inicio_sii") == "si":
        raise HTTPException(
            status_code=409,
            detail="Tu formalización ya está completa",
        )

    pending = get_pending_milestone(user)
    # Evita avanzar un hito distinto al que ve el usuario (doble clic o un
    # avance hecho mientras tanto por WhatsApp).
    if pending is None or pending.get("id") != milestone_id:
        raise HTTPException(
            status_code=409,
            detail="Este hito ya no es tu paso pendiente. Recarga el panel.",
        )

    await run_in_threadpool(mark_hito_done, user, save_user)
    return {"title": pending.get("title"), **_progress(user)}


@router.post("/undo")
async def undo_last_milestone(
    request: Request,
    financial_session: str | None = Cookie(
        default=None,
        alias=_SESSION_COOKIE,
    ),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    user = await _authenticated_user(request, financial_session)
    await _require_csrf(request, financial_session, csrf_token)

    last_completed = get_last_completed_milestone(user)
    if user.get("inicio_sii") == "si" or last_completed is None:
        raise HTTPException(
            status_code=409,
            detail="No hay ningún hito completado que deshacer",
        )

    await run_in_threadpool(revert_last_hito, user, save_user)
    return {"title": last_completed.get("title"), **_progress(user)}
