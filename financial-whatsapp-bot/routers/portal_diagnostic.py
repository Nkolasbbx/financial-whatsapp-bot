"""API privada del diagnóstico: sesión, CSRF y versión esperada obligatorios."""
from fastapi import APIRouter, Cookie, Header, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from config import FORMALIZATION_DIAGNOSTICS_ENABLED
from db.users import get_user
from db.formalization_diagnostics import get_formalization_context, FormalizationConflictError, FormalizationNotFoundError
from services.portal_auth import get_session_phone, validate_csrf_token
from services import portal_diagnostic as service
from schemas.portal_diagnostic import DiagnosticAdjustments

router = APIRouter(prefix="/portal/api/diagnostic", tags=["portal-diagnostic"])


async def authenticated(request, session):
    if not FORMALIZATION_DIAGNOSTICS_ENABLED or request.app.state.redis is None:
        raise HTTPException(503, "El diagnóstico no está disponible temporalmente.")
    phone = await get_session_phone(request.app.state.redis, session)
    if not phone:
        raise HTTPException(401, "Tu sesión venció. Pide otro acceso por WhatsApp.")
    user = await run_in_threadpool(get_user, phone)
    if not user or not user.get("id"):
        raise HTTPException(404, "No encontramos tu perfil.")
    return str(user["id"])


@router.get("")
async def get_diagnostic(request: Request, response: Response, financial_session: str | None = Cookie(default=None)):
    user_id = await authenticated(request, financial_session)
    response.headers["Cache-Control"] = "no-store"
    try:
        context = await run_in_threadpool(get_formalization_context, user_id)
        return service.view(context)
    except Exception as error:
        raise HTTPException(503, "No pudimos consultar tu diagnóstico. Inténtalo nuevamente.") from error


@router.put("")
async def save_diagnostic(payload: DiagnosticAdjustments, request: Request, response: Response,
                          financial_session: str | None = Cookie(default=None),
                          csrf_token: str | None = Header(default=None, alias="X-CSRF-Token")):
    user_id = await authenticated(request, financial_session)
    if not await validate_csrf_token(request.app.state.redis, financial_session, csrf_token):
        raise HTTPException(403, "La solicitud no es válida. Recarga el panel.")
    response.headers["Cache-Control"] = "no-store"
    try:
        return await run_in_threadpool(service.update, user_id, payload)
    except FormalizationNotFoundError as error:
        raise HTTPException(404, "No encontramos ese diagnóstico.") from error
    except FormalizationConflictError as error:
        raise HTTPException(409, "El diagnóstico cambió en otra sesión o en WhatsApp. Recarga antes de editar; no sobrescribimos esa versión.") from error
    except service.RecalculationPending as error:
        raise HTTPException(503, "Tus ajustes quedaron guardados, pero falta recalcular. Recarga y pulsa Guardar y recalcular nuevamente.") from error
    except ValueError as error:
        raise HTTPException(422, "Revisa los montos, abonos, frecuencia y fechas. Usa una fecha futura para pagos pendientes y montos enteros no negativos.") from error
    except Exception as error:
        raise HTTPException(503, "No pudimos confirmar el guardado. Recarga para comprobar el estado antes de reintentar.") from error
