"""Flujo conversacional determinista para evaluar fondos (HdU05)."""

from __future__ import annotations

import logging
import re

from core.fondos import (
    evaluate_available_funds,
    evaluate_fund,
    format_fund_evaluation,
    format_funds_summary,
    fund_applies_to_user,
)
from db.fondos import (
    UNKNOWN_ANSWER,
    cancel_fund_session,
    find_active_fund,
    finish_fund_session,
    get_active_fund_session,
    get_fund_answer_records,
    get_fund_by_id,
    get_requirement_definitions,
    normalize_fund_text,
    save_fund_answer,
    start_fund_session,
    update_fund_session,
)

logger = logging.getLogger("financial")

FUND_SELECT_PREFIX = "fund_select:"
FUND_ANSWER_PREFIX = "fund_answer:"
FUND_UPDATE_DATA_ID = "fund_update_data"
FUND_UPDATE_FIELD_PREFIX = "fund_update_field:"
FUND_CANCEL_COMMANDS = {
    "cancelar",
    "cancelar evaluacion",
    "cancelar evaluación",
    "salir de fondos",
    "fund cancel",
}
FUND_ENTRY_TERMS = {
    "fondo",
    "fondos",
    "postular",
    "postular fondos",
    "postular a fondos",
    "sercotec",
    "corfo",
    "financiamiento",
    "menu fondo",
}
FUND_NAME_HINTS = {"capital", "semilla", "abeja", "pioneras", "crece"}

_INVALID_ANSWER = object()


def _fund_list_widget(user: dict, prefix: str = "") -> dict | str:
    evaluations = evaluate_available_funds(user)
    if not evaluations:
        return (
            f"{prefix}⚠️ No encontré fondos vigentes compatibles con tu perfil "
            "en este momento. Puedes volver a consultar más adelante."
        )

    options = [(FUND_UPDATE_DATA_ID, "🔄 Actualizar datos")]
    for evaluation in evaluations[:9]:
        fund = evaluation["fund"]
        identifier = fund.get("slug") or fund.get("id") or fund["nombre"]
        options.append(
            (
                f"{FUND_SELECT_PREFIX}{identifier}",
                f"{fund.get('emoji', '💰')} {fund['nombre']}",
            )
        )

    body = (
        f"{prefix}"
        f"{format_funds_summary(evaluations, max_length=max(300, 1000 - len(prefix)))}"
    )
    return {
        "type": "list",
        "body": body,
        "button_text": "Elegir fondo",
        "options": options,
    }


def _definition_label(field_key: str, definition: dict) -> str:
    """Obtiene un nombre corto para mostrar un dato editable en WhatsApp."""
    return (
        definition.get("label")
        or definition.get("texto")
        or field_key.replace("_", " ").capitalize()
    )


def _update_data_widget(user: dict) -> dict | str:
    """Lista solo respuestas editables usadas al evaluar postulaciones."""
    definitions = get_requirement_definitions()
    editable = [
        (field_key, definition)
        for field_key, definition in definitions.items()
        if definition.get("source_type") == "user_answer"
    ]
    if not editable:
        return (
            "No encontré datos de postulación editables en este momento. "
            "Puedes volver a intentarlo escribiendo *postular fondos*."
        )

    start_fund_session(user["id"])
    return {
        "type": "list",
        "body": (
            "🔄 *Actualizar datos de postulación*\n\n"
            "Selecciona el requisito que cambió. Esto no modificará tu "
            "rubro, comuna, estado SII ni roadmap."
        ),
        "button_text": "Elegir dato",
        "options": [
            (
                f"{FUND_UPDATE_FIELD_PREFIX}{field_key}",
                _definition_label(field_key, definition),
            )
            for field_key, definition in editable[:10]
        ],
    }


def _start_data_update(user: dict, message: str) -> dict | str:
    """Inicia la edición de una respuesta concreta de postulación."""
    field_key = message.strip()[len(FUND_UPDATE_FIELD_PREFIX):]
    definitions = get_requirement_definitions([field_key])
    definition = definitions.get(field_key)
    if definition is None or definition.get("source_type") != "user_answer":
        return _update_data_widget(user)

    start_fund_session(user["id"])
    update_fund_session(
        user["id"],
        status="collecting_data",
        pending_field_key=field_key,
    )
    return _question_widget(
        definition,
        f"Actualizarás *{_definition_label(field_key, definition)}*.\n\n",
    )


def _save_updated_data(user: dict, message: str, field_key: str) -> dict | str:
    """Valida y reemplaza una respuesta guardada en fund_user_answers."""
    definitions = get_requirement_definitions([field_key])
    definition = definitions.get(field_key)
    if definition is None or definition.get("source_type") != "user_answer":
        cancel_fund_session(user["id"])
        return _update_data_widget(user)

    parsed_answer = _parse_answer(message, definition)
    if parsed_answer is _INVALID_ANSWER:
        return _question_widget(
            definition,
            "No pude interpretar esa respuesta.\n\n",
        )

    save_fund_answer(user["id"], field_key, parsed_answer)
    finish_fund_session(user["id"])
    label = _definition_label(field_key, definition)
    return _fund_list_widget(
        user,
        f"✅ Actualicé *{label}*. Recalculé tus fondos con este dato.\n\n",
    )


def start_fund_flow(user: dict) -> dict | str:
    """Inicia la selección de fondos para un usuario registrado."""
    user_id = user.get("id")
    if not user_id:
        return "No pude identificar tu perfil. Escribe *menu* e intenta nuevamente."
    start_fund_session(user_id)
    return _fund_list_widget(user)


def _question_widget(requirement: dict, prefix: str = "") -> dict | str:
    question = requirement.get("question") or "Necesito confirmar este requisito."
    body = f"{prefix}📋 *Evaluación de requisitos*\n\n{question}"
    if requirement.get("answer_type") != "boolean":
        unit = (requirement.get("evaluation_rule") or {}).get("unit")
        suffix = f" en {unit}" if unit else ""
        return f"{body}\n\nResponde con un número{suffix}."

    options = requirement.get("options") or [
        {"id": "yes", "title": "Sí", "value": True},
        {"id": "no", "title": "No", "value": False},
        {"id": "unknown", "title": "No lo sé", "value": None},
    ]
    return {
        "type": "buttons",
        "body": body,
        "options": [
            (f"{FUND_ANSWER_PREFIX}{option['id']}", option["title"])
            for option in options[:3]
        ],
    }


def _parse_numeric_answer(message: str):
    value = message.casefold().replace("uf", "").strip()
    raw_value = re.sub(r"[^0-9,.-]", "", value)
    if not raw_value:
        return _INVALID_ANSWER

    if re.fullmatch(r"\d{1,3}(\.\d{3})+", raw_value):
        raw_value = raw_value.replace(".", "")
    elif "," in raw_value and "." not in raw_value:
        raw_value = raw_value.replace(",", ".")
    elif "," in raw_value and "." in raw_value:
        raw_value = raw_value.replace(".", "").replace(",", ".")

    try:
        numeric_value = float(raw_value)
    except ValueError:
        return _INVALID_ANSWER
    if numeric_value < 0:
        return _INVALID_ANSWER
    return int(numeric_value) if numeric_value.is_integer() else numeric_value


def _parse_answer(message: str, definition: dict):
    if definition.get("answer_type") == "number":
        return _parse_numeric_answer(message)

    normalized = normalize_fund_text(message)
    answer_id = normalized
    raw_lower = message.strip().lower()
    if raw_lower.startswith(FUND_ANSWER_PREFIX):
        answer_id = raw_lower.removeprefix(FUND_ANSWER_PREFIX)

    for option in definition.get("options") or []:
        if answer_id in {
            normalize_fund_text(option.get("id")),
            normalize_fund_text(option.get("title")),
        }:
            return option.get("value")

    if normalized in {"si", "s", "yes", "confirmo"}:
        return True
    if normalized in {"no", "n"}:
        return False
    if normalized in {"no se", "no lo se", "prefiero omitir", "unknown"}:
        return None
    return _INVALID_ANSWER


def _evaluate_selected_fund(user: dict, fund: dict) -> dict | str:
    definitions = get_requirement_definitions()
    records = get_fund_answer_records(user["id"])
    answers = {
        key: None if value == UNKNOWN_ANSWER else value
        for key, value in records.items()
    }
    evaluation = evaluate_fund(
        fund,
        user,
        answers,
        definitions,
        answered_keys=set(records),
    )

    if evaluation["missing_questions"]:
        requirement = evaluation["missing_questions"][0]
        update_fund_session(
            user["id"],
            status="collecting_data",
            pending_field_key=requirement["clave"],
        )
        return _question_widget(requirement)

    finish_fund_session(user["id"])
    return format_fund_evaluation(evaluation, user)


def _select_fund(user: dict, message: str) -> dict | str:
    raw_selection = message.strip()
    if raw_selection.lower().startswith(FUND_SELECT_PREFIX):
        raw_selection = raw_selection[len(FUND_SELECT_PREFIX):]

    fund = find_active_fund(raw_selection)
    if fund is None or not fund_applies_to_user(fund, user):
        return _fund_list_widget(
            user,
            "No pude identificar ese fondo entre los disponibles.\n\n",
        )

    start_fund_session(user["id"], fund["id"])
    return _evaluate_selected_fund(user, fund)


def should_handle_fund_message(user: dict, message: str) -> bool:
    """Determina si el mensaje pertenece al flujo de fondos."""
    normalized = normalize_fund_text(message)
    raw_lower = message.strip().lower()
    if raw_lower == FUND_UPDATE_DATA_ID:
        return True
    if raw_lower.startswith(
        (FUND_SELECT_PREFIX, FUND_ANSWER_PREFIX, FUND_UPDATE_FIELD_PREFIX)
    ):
        return True
    if normalized in FUND_CANCEL_COMMANDS:
        return True
    if any(term in normalized for term in FUND_ENTRY_TERMS):
        return True
    if any(hint in normalized.split() for hint in FUND_NAME_HINTS):
        try:
            return find_active_fund(message) is not None
        except Exception as error:
            logger.error("No se pudo buscar el fondo mencionado: %s", error)
            return False

    user_id = user.get("id")
    if not user_id:
        return False
    try:
        return get_active_fund_session(user_id) is not None
    except Exception as error:
        logger.error("No se pudo consultar la sesión de fondos: %s", error)
        return False


def handle_fund_message(user: dict, message: str) -> dict | str:
    """Procesa inicio, selección y respuestas de una evaluación de fondos."""
    user_id = user.get("id")
    if not user_id:
        return "No pude identificar tu perfil. Escribe *menu* e intenta nuevamente."

    normalized = normalize_fund_text(message)
    raw_lower = message.strip().lower()
    if normalized in FUND_CANCEL_COMMANDS:
        cancel_fund_session(user_id)
        return (
            "Evaluación de fondos cancelada. Puedes retomarla cuando quieras "
            "escribiendo *postular fondos*."
        )

    if raw_lower == FUND_UPDATE_DATA_ID:
        return _update_data_widget(user)

    if raw_lower.startswith(FUND_UPDATE_FIELD_PREFIX):
        return _start_data_update(user, message)

    if "postular" in normalized or normalized in {
        "fondo",
        "fondos",
        "menu fondo",
    }:
        return start_fund_flow(user)

    session = get_active_fund_session(user_id)
    if raw_lower.startswith(FUND_SELECT_PREFIX):
        return _select_fund(user, message)

    if (
        session is not None
        and not session.get("fondo_id")
        and session.get("pending_field_key")
    ):
        return _save_updated_data(
            user,
            message,
            session["pending_field_key"],
        )

    if session is None:
        direct_fund = find_active_fund(message)
        if direct_fund is not None:
            return _select_fund(user, message)
        return start_fund_flow(user)

    if session.get("status") == "selecting" or not session.get("fondo_id"):
        return _select_fund(user, message)

    fund = get_fund_by_id(session["fondo_id"])
    if fund is None:
        cancel_fund_session(user_id)
        return start_fund_flow(user)

    pending_key = session.get("pending_field_key")
    if not pending_key:
        return _evaluate_selected_fund(user, fund)

    definitions = get_requirement_definitions([pending_key])
    definition = definitions.get(pending_key)
    if definition is None:
        cancel_fund_session(user_id)
        return "No pude cargar el requisito pendiente. Inténtalo nuevamente."

    parsed_answer = _parse_answer(message, definition)
    if parsed_answer is _INVALID_ANSWER:
        requirement = {
            "question": definition.get("question"),
            "answer_type": definition.get("answer_type"),
            "options": definition.get("options") or [],
            "evaluation_rule": definition.get("evaluation_rule") or {},
        }
        return _question_widget(
            requirement,
            "No pude interpretar esa respuesta.\n\n",
        )

    save_fund_answer(user_id, pending_key, parsed_answer)
    update_fund_session(user_id, clear_pending_field=True)
    return _evaluate_selected_fund(user, fund)
