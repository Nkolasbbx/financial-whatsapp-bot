"""API privada de consulta financiera del panel del emprendedor."""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Cookie, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from config import REMINDER_TIMEZONE
from db.financial_movements import (
    get_financial_month_summary,
    list_financial_movements,
)
from db.users import get_user
from schemas.financial_movements import (
    FinancialMonthSummary,
    PortalFinancialDashboardResponse,
    PortalFinancialMovement,
)
from services.portal_auth import get_session_phone


router = APIRouter(prefix="/portal/api/finances", tags=["portal-finances"])

_SESSION_COOKIE = "financial_session"
_MONTH_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")
_MOVEMENT_LIMIT = 100


def _local_today() -> date:
    try:
        return datetime.now(ZoneInfo(REMINDER_TIMEZONE)).date()
    except Exception:
        return date.today()


def month_range(month: str | None) -> tuple[date, date]:
    """Convierte YYYY-MM en un rango mensual semiabierto [inicio, fin)."""
    if month is None:
        start = _local_today().replace(day=1)
    else:
        match = _MONTH_PATTERN.fullmatch(month.strip())
        if not match:
            raise HTTPException(
                status_code=422,
                detail="El mes debe tener formato YYYY-MM",
            )
        try:
            start = date(int(match.group(1)), int(match.group(2)), 1)
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail="El mes seleccionado no es válido",
            ) from error

    if start.month == 12:
        end = date(start.year + 1, 1, 1)
    else:
        end = date(start.year, start.month + 1, 1)
    return start, end


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
    if not user or not user.get("id"):
        raise HTTPException(status_code=404, detail="No encontramos el perfil")
    return user


@router.get(
    "/dashboard",
    response_model=PortalFinancialDashboardResponse,
)
async def financial_dashboard(
    request: Request,
    month: str | None = Query(default=None),
    financial_session: str | None = Cookie(default=None),
):
    """Retorna el resumen y los movimientos del mes del usuario autenticado."""
    user = await _authenticated_user(request, financial_session)
    month_start, month_end = month_range(month)
    user_id = str(user["id"])

    summary_raw, movements_raw = await asyncio.gather(
        run_in_threadpool(
            get_financial_month_summary,
            user_id,
            month_start,
            month_end,
        ),
        run_in_threadpool(
            list_financial_movements,
            user_id,
            start_date=month_start,
            end_date=month_end,
            limit=_MOVEMENT_LIMIT,
        ),
    )

    summary = FinancialMonthSummary.model_validate(summary_raw)
    movements = [
        PortalFinancialMovement.model_validate(movement)
        for movement in movements_raw
    ]
    return PortalFinancialDashboardResponse(
        **summary.model_dump(),
        movements=movements,
    )
