"""API privada de evaluación de fondos concursables del panel (HdU13).

Expone la misma evaluación que el flujo de WhatsApp (``core.fondos``) y
permite actualizar las respuestas de postulación (``fund_user_answers``).
Igual que en el bot, actualizar estos datos no modifica rubro, comuna,
estado SII ni roadmap.
"""

from __future__ import annotations

import math
from datetime import date

from fastapi import APIRouter, Cookie, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from core.fondos import evaluate_available_funds, get_requirement_urgency
from db.fondos import (
    UNKNOWN_ANSWER,
    get_fund_answer_records,
    get_requirement_definitions,
    save_fund_answers,
)
from db.users import get_user
from services.portal_auth import get_session_phone, validate_csrf_token


router = APIRouter(prefix="/portal/api/funds", tags=["portal-funds"])

_SESSION_COOKIE = "financial_session"
_DEFAULT_BOOLEAN_OPTIONS = [
    {"id": "yes", "title": "Sí", "value": True},
    {"id": "no", "title": "No", "value": False},
    {"id": "unknown", "title": "No lo sé", "value": None},
]


class FundAnswersUpdateRequest(BaseModel):
    """Respuestas a reemplazar. Las de sí/no se envían como id de opción
    (``yes``/``no``/``unknown``); las numéricas como número o ``null``."""

    answers: dict[str, str | float | None] = Field(min_length=1, max_length=30)


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
            detail="La solicitud de fondos no es válida",
        )


def _serialize_requirement(
    requirement: dict,
    blocking_keys: set[str],
    days_remaining: int | None,
) -> dict:
    status = {True: "met", False: "failed"}.get(requirement["cumple"], "unknown")
    urgency = None
    if status == "failed" and requirement.get("plazo_dias") is not None:
        urgency = get_requirement_urgency(requirement, days_remaining)["label"]
    return {
        "key": requirement.get("clave"),
        "text": requirement.get("texto") or requirement.get("clave") or "",
        "status": status,
        "blocking": requirement.get("clave") in blocking_keys,
        "recommendation": requirement.get("recomendacion"),
        "estimated_time": requirement.get("plazo"),
        "urgency": urgency,
    }


def _serialize_evaluation(evaluation: dict) -> dict:
    fund = evaluation["fund"]
    closing_date = fund.get("fecha_cierre")
    blocking_keys = {
        requirement.get("clave")
        for requirement in evaluation["blocking_failures"]
    }
    return {
        "id": str(fund.get("id") or fund.get("slug") or fund.get("nombre")),
        "name": fund.get("nombre") or "Fondo",
        "emoji": fund.get("emoji") or "💰",
        "entity": fund.get("entidad"),
        "link": fund.get("link") or None,
        "max_amount": fund.get("monto_max"),
        "closing_date": (
            closing_date.isoformat() if isinstance(closing_date, date) else None
        ),
        "days_remaining": evaluation["days_remaining"],
        "is_open": evaluation["is_open"],
        "percentage": evaluation["percentage"],
        "met": evaluation["met"],
        "failed": evaluation["failed"],
        "unknown": evaluation["unknown"],
        "total": evaluation["total"],
        "blocked": bool(evaluation["blocking_failures"]),
        "requirements": [
            _serialize_requirement(
                requirement,
                blocking_keys,
                evaluation["days_remaining"],
            )
            for requirement in evaluation["requirements"]
        ],
    }


def _editable_definitions(all_definitions: dict[str, dict]) -> dict[str, dict]:
    return {
        field_key: definition
        for field_key, definition in all_definitions.items()
        if definition.get("source_type") == "user_answer"
    }


def _build_funds_payload(
    user: dict,
    all_definitions: dict[str, dict] | None = None,
) -> dict:
    """Evaluación de fondos + formulario de datos editables del usuario.

    Definiciones y respuestas se leen una sola vez y se comparten con la
    evaluación para no repetir consultas a Supabase.
    """
    if all_definitions is None:
        all_definitions = get_requirement_definitions()
    records = get_fund_answer_records(user["id"])
    answers = {
        field_key: None if value == UNKNOWN_ANSWER else value
        for field_key, value in records.items()
    }
    evaluations = evaluate_available_funds(
        user,
        definitions=all_definitions,
        answers=answers,
    )
    definitions = _editable_definitions(all_definitions)

    # Solo se piden los datos que usan los fondos que ve el usuario; si no
    # hay fondos vigentes, se muestran todos para que igual pueda completarlos.
    used_keys = {
        requirement.get("clave")
        for evaluation in evaluations
        for requirement in evaluation["requirements"]
    }
    relevant = [
        (field_key, definition)
        for field_key, definition in definitions.items()
        if not used_keys or field_key in used_keys
    ]

    fields = []
    for field_key, definition in relevant:
        answer_type = (
            "number" if definition.get("answer_type") == "number" else "boolean"
        )
        has_record = field_key in records
        stored = records.get(field_key)
        answered = has_record and stored != UNKNOWN_ANSWER
        field = {
            "key": field_key,
            "label": (
                definition.get("label")
                or field_key.replace("_", " ").capitalize()
            ),
            "question": definition.get("question"),
            "type": answer_type,
            "value": stored if answered else None,
            "answered": answered,
        }
        if answer_type == "number":
            field["unit"] = (definition.get("evaluation_rule") or {}).get("unit")
        else:
            options = definition.get("options") or _DEFAULT_BOOLEAN_OPTIONS
            field["options"] = [
                {"id": option.get("id"), "title": option.get("title")}
                for option in options
                if option.get("id")
            ]
            # "No lo sé" queda guardado como UNKNOWN_ANSWER y corresponde a
            # la opción cuyo valor es None.
            effective = stored if answered else None
            field["selected"] = next(
                (
                    option.get("id")
                    for option in options
                    if has_record and option.get("value") == effective
                ),
                None,
            )
        fields.append(field)

    return {
        "funds": [_serialize_evaluation(evaluation) for evaluation in evaluations],
        "fields": fields,
    }


def _parse_answer(definition: dict, raw_value):
    """Convierte el valor del formulario al formato que evalúa core.fondos."""
    if definition.get("answer_type") == "number":
        if raw_value is None or raw_value == "":
            return None
        try:
            number = float(raw_value)
        except (TypeError, ValueError) as error:
            raise ValueError("debe ser un número") from error
        if not math.isfinite(number) or number < 0:
            raise ValueError("debe ser un número positivo")
        return int(number) if number.is_integer() else number

    options = definition.get("options") or _DEFAULT_BOOLEAN_OPTIONS
    for option in options:
        if option.get("id") == raw_value:
            return option.get("value")
    raise ValueError("la opción elegida no es válida")


@router.get("")
async def list_funds(
    request: Request,
    financial_session: str | None = Cookie(
        default=None,
        alias=_SESSION_COOKIE,
    ),
):
    """Retorna los fondos evaluados y los datos de postulación editables."""
    user = await _authenticated_user(request, financial_session)
    return await run_in_threadpool(_build_funds_payload, user)


@router.put("/answers")
async def update_answers(
    payload: FundAnswersUpdateRequest,
    request: Request,
    financial_session: str | None = Cookie(
        default=None,
        alias=_SESSION_COOKIE,
    ),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    """Reemplaza respuestas de postulación y retorna la evaluación nueva."""
    user = await _authenticated_user(request, financial_session)
    await _require_csrf(request, financial_session, csrf_token)

    all_definitions = await run_in_threadpool(get_requirement_definitions)
    definitions = _editable_definitions(all_definitions)
    parsed: dict[str, object] = {}
    errors: list[str] = []
    for field_key, raw_value in payload.answers.items():
        definition = definitions.get(field_key)
        if definition is None:
            errors.append(f"El dato «{field_key}» no se puede editar")
            continue
        try:
            parsed[field_key] = _parse_answer(definition, raw_value)
        except ValueError as error:
            label = definition.get("label") or field_key
            errors.append(f"{label}: {error}")

    if errors:
        raise HTTPException(status_code=422, detail=". ".join(errors))

    await run_in_threadpool(save_fund_answers, user["id"], parsed)

    return await run_in_threadpool(_build_funds_payload, user, all_definitions)
