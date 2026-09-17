"""Flujo conversacional para registrar y resumir movimientos financieros."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from config import REMINDER_TIMEZONE
from core.financial_parser import (
    classify_category,
    looks_like_financial_movement,
    normalize_financial_text,
)
from core.menu import MENU_BUTTON
from db.financial_movements import (
    clear_financial_session,
    confirm_financial_movement,
    get_financial_month_summary,
    get_financial_session,
    get_last_financial_movement,
    soft_delete_financial_movement,
    start_financial_session,
    update_financial_session,
)
from schemas.financial_movements import (
    FinancialMonthSummary,
    FinancialMovementConfirm,
    FinancialMovementDraft,
)


FINANCIAL_MENU_ID = "menu_finances"
FINANCIAL_NEW_INCOME_ID = "finance_new_income"
FINANCIAL_NEW_EXPENSE_ID = "finance_new_expense"
FINANCIAL_SUMMARY_ID = "finance_month_summary"
FINANCIAL_CONFIRM_ID = "finance_confirm"
FINANCIAL_EDIT_ID = "finance_edit"
FINANCIAL_CANCEL_ID = "finance_cancel"
FINANCIAL_TYPE_INCOME_ID = "finance_type_income"
FINANCIAL_TYPE_EXPENSE_ID = "finance_type_expense"
FINANCIAL_CORRECT_LAST_ID = "finance_correct_last"
FINANCIAL_LAST_EDIT_ID = "finance_last_edit"
FINANCIAL_LAST_DELETE_ID = "finance_last_delete"
FINANCIAL_DELETE_CONFIRM_ID = "finance_delete_confirm"
FINANCIAL_DELETE_CANCEL_ID = "finance_delete_cancel"

FINANCIAL_PARSE_TASK = "__FINANCIAL_PARSE__"

_ENTRY_COMMANDS = {
    "registrar movimiento",
    "registrar movimientos",
    "mis movimientos",
    "movimientos",
    "mis finanzas",
    "ingresos y gastos",
    "registrar ingreso",
    "registrar gasto",
    "nuevo ingreso",
    "nuevo gasto",
}

_SUMMARY_COMMANDS = {
    "resumen del mes",
    "resumen mensual",
    "mi resumen mensual",
    "balance del mes",
    "como me fue este mes",
    "cuanto gane este mes",
    "mis ingresos y gastos",
}

_CORRECTION_COMMANDS = {
    "corregir ultimo movimiento",
    "editar ultimo movimiento",
    "eliminar ultimo movimiento",
    "corregir movimiento",
    "corregir ultimo",
}

_CANCEL_COMMANDS = {
    "cancelar movimiento",
    "cancelar finanzas",
    "salir de finanzas",
}

_TEXT_ACTION_IDS = {
    "confirmar": FINANCIAL_CONFIRM_ID,
    "editar": FINANCIAL_EDIT_ID,
    "cancelar": FINANCIAL_CANCEL_ID,
    "ingreso": FINANCIAL_TYPE_INCOME_ID,
    "gasto": FINANCIAL_TYPE_EXPENSE_ID,
    "si eliminar": FINANCIAL_DELETE_CONFIRM_ID,
    "no conservar": FINANCIAL_DELETE_CANCEL_ID,
}

_EXTERNAL_COMMANDS = {
    "menu",
    "menu principal",
    "ayuda",
    "roadmap",
    "mi roadmap",
    "postular fondos",
    "postular a fondos",
    "calendario",
    "mi calendario",
    "activar recordatorios",
    "pausar recordatorios",
    "reiniciar",
    "reset",
}

_CATEGORY_LABELS = {
    "ventas": "Ventas",
    "servicios": "Servicios",
    "aportes_capital": "Aportes o capital",
    "otros_ingresos": "Otros ingresos",
    "insumos_mercaderia": "Insumos y mercadería",
    "transporte": "Transporte",
    "arriendo_servicios": "Arriendo y servicios",
    "permisos_tramites": "Permisos y trámites",
    "marketing": "Marketing",
    "equipamiento": "Equipamiento",
    "otros_gastos": "Otros gastos",
}


def _currency(value: int) -> str:
    sign = "-" if value < 0 else ""
    formatted = f"{abs(value):,.0f}".replace(",", ".")
    return f"{sign}${formatted}"


def _movement_type_label(value: str) -> str:
    return "Ingreso" if value == "income" else "Gasto"


def _category_label(value: str) -> str:
    return _CATEGORY_LABELS.get(value, value.replace("_", " ").capitalize())


def _local_today() -> date:
    try:
        return datetime.now(ZoneInfo(REMINDER_TIMEZONE)).date()
    except Exception:
        return date.today()


def _month_range(today: date | None = None) -> tuple[date, date]:
    current = today or _local_today()
    start = current.replace(day=1)
    if start.month == 12:
        end = date(start.year + 1, 1, 1)
    else:
        end = date(start.year, start.month + 1, 1)
    return start, end


def _month_name(value: date) -> str:
    names = (
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre",
        "diciembre",
    )
    return f"{names[value.month - 1]} de {value.year}"


def financial_menu(prefix: str = "") -> dict:
    return {
        "type": "buttons",
        "body": (
            f"{prefix}💰 *Ingresos y gastos*\n\n"
            "Registra movimientos escribiéndolos de forma natural.\n\n"
            "Ejemplos:\n"
            "• Hoy vendí $40.000 en empanadas\n"
            "• Gasté 15 lucas en harina\n"
            "• Resumen del mes"
        ),
        "options": [
            (FINANCIAL_NEW_INCOME_ID, "Registrar ingreso"),
            (FINANCIAL_NEW_EXPENSE_ID, "Registrar gasto"),
            (FINANCIAL_SUMMARY_ID, "Resumen del mes"),
        ],
    }


def _new_movement_prompt(movement_type: str) -> dict:
    label = "ingreso" if movement_type == "income" else "gasto"
    example = (
        "Hoy vendí $40.000 en empanadas"
        if movement_type == "income"
        else "Hoy gasté $15.000 en harina"
    )
    return {
        "type": "buttons",
        "body": (
            f"Escribe tu *{label}* en una sola frase, indicando el monto "
            "y el concepto.\n\n"
            f"Ejemplo: _{example}_"
        ),
        "options": [
            (FINANCIAL_CANCEL_ID, "Cancelar"),
            MENU_BUTTON[0],
        ],
    }


def missing_data_response(extraction: FinancialMovementDraft) -> dict:
    missing = extraction.required_missing_fields()
    if "movement_type" in missing:
        return {
            "type": "buttons",
            "body": "¿Este movimiento fue un *ingreso* o un *gasto*?",
            "options": [
                (FINANCIAL_TYPE_INCOME_ID, "Ingreso"),
                (FINANCIAL_TYPE_EXPENSE_ID, "Gasto"),
                (FINANCIAL_CANCEL_ID, "Cancelar"),
            ],
        }
    if "amount" in missing:
        body = (
            "¿Cuál fue el *monto total en pesos*?\n\n"
            "Por ejemplo: _$40.000_ o _40 mil pesos_."
        )
    elif "description" in missing:
        body = (
            "¿A qué correspondió el movimiento?\n\n"
            "Por ejemplo: _venta de empanadas_ o _compra de harina_."
        )
    else:
        body = (
            "Me falta información para registrar el movimiento. "
            "Escríbelo nuevamente indicando tipo, monto y concepto."
        )
    return {
        "type": "buttons",
        "body": body,
        "options": [
            (FINANCIAL_CANCEL_ID, "Cancelar"),
            MENU_BUTTON[0],
        ],
    }


def confirmation_response(
    movement: FinancialMovementConfirm,
    *,
    editing: bool = False,
) -> dict:
    title = "Movimiento corregido" if editing else "Revisemos el movimiento"
    return {
        "type": "buttons",
        "body": (
            f"🧾 *{title}*\n\n"
            f"• *Tipo:* {_movement_type_label(movement.movement_type)}\n"
            f"• *Monto:* {_currency(movement.amount)}\n"
            f"• *Categoría:* {_category_label(movement.category)}\n"
            f"• *Descripción:* {movement.description}\n"
            f"• *Fecha:* {movement.occurred_on.strftime('%d/%m/%Y')}\n\n"
            "¿Está correcto?"
        ),
        "options": [
            (FINANCIAL_CONFIRM_ID, "Confirmar"),
            (FINANCIAL_EDIT_ID, "Editar"),
            (FINANCIAL_CANCEL_ID, "Cancelar"),
        ],
    }


def _movement_detail(movement: dict) -> str:
    occurred_on = date.fromisoformat(str(movement["occurred_on"])[:10])
    return (
        f"• *Tipo:* {_movement_type_label(movement['movement_type'])}\n"
        f"• *Monto:* {_currency(int(movement['amount']))}\n"
        f"• *Categoría:* {_category_label(movement['category'])}\n"
        f"• *Descripción:* {movement['description']}\n"
        f"• *Fecha:* {occurred_on.strftime('%d/%m/%Y')}"
    )


def _correction_widget(user_id: str) -> dict:
    movement = get_last_financial_movement(user_id)
    if not movement:
        return financial_menu(
            "Todavía no tienes movimientos para corregir.\n\n"
        )

    start_financial_session(
        user_id,
        "choosing_correction",
        target_movement_id=movement["id"],
    )
    return {
        "type": "buttons",
        "body": (
            "✏️ *Último movimiento registrado*\n\n"
            f"{_movement_detail(movement)}\n\n"
            "¿Qué quieres hacer?"
        ),
        "options": [
            (FINANCIAL_LAST_EDIT_ID, "Editar"),
            (FINANCIAL_LAST_DELETE_ID, "Eliminar"),
            (FINANCIAL_CANCEL_ID, "Cancelar"),
        ],
    }


def _no_movements_message(user: dict, month_start: date) -> dict:
    rubro = normalize_financial_text(user.get("rubro") or user.get("rubro_raw"))
    if "alimento" in rubro or "comida" in rubro:
        examples = (
            "• Hoy vendí $40.000 en empanadas\n"
            "• Gasté $18.500 en harina y aceite\n"
            "• Pagué $12.000 por transporte"
        )
    elif "textil" in rubro or "ropa" in rubro:
        examples = (
            "• Hoy vendí $65.000 en ropa\n"
            "• Gasté $25.000 en telas\n"
            "• Pagué $10.000 por un despacho"
        )
    else:
        examples = (
            "• Hoy recibí $40.000 por una venta\n"
            "• Gasté $15.000 en insumos\n"
            "• Pagué $8.000 por transporte"
        )
    return {
        "type": "buttons",
        "body": (
            f"📊 Aún no tienes movimientos registrados en "
            f"*{_month_name(month_start)}*.\n\n"
            "Puedes comenzar escribiendo:\n"
            f"{examples}"
        ),
        "options": [
            (FINANCIAL_NEW_INCOME_ID, "Registrar ingreso"),
            (FINANCIAL_NEW_EXPENSE_ID, "Registrar gasto"),
            MENU_BUTTON[0],
        ],
    }


def _summary_widget(user: dict) -> dict:
    start, end = _month_range()
    raw_summary = get_financial_month_summary(user["id"], start, end)
    summary = FinancialMonthSummary.model_validate(raw_summary)
    if summary.movement_count == 0:
        return _no_movements_message(user, start)

    income_categories = "\n".join(
        f"• {_category_label(item.category)}: {_currency(item.total)}"
        for item in summary.income_categories
    ) or "• Sin ingresos"
    expense_categories = "\n".join(
        f"• {_category_label(item.category)}: {_currency(item.total)}"
        for item in summary.expense_categories
    ) or "• Sin gastos"
    net_emoji = "🟢" if summary.net_total >= 0 else "🔴"

    return {
        "type": "buttons",
        "body": (
            f"📊 *Resumen de {_month_name(start)}*\n\n"
            f"Ingresos: *{_currency(summary.income_total)}*\n"
            f"Gastos: *{_currency(summary.expense_total)}*\n"
            f"{net_emoji} Resultado neto: *{_currency(summary.net_total)}*\n"
            f"Movimientos: {summary.movement_count}\n\n"
            f"*Principales ingresos*\n{income_categories}\n\n"
            f"*Principales gastos*\n{expense_categories}"
        ),
        "options": [
            (FINANCIAL_CORRECT_LAST_ID, "Corregir último"),
            (FINANCIAL_NEW_INCOME_ID, "Nuevo ingreso"),
            MENU_BUTTON[0],
        ],
    }


def is_financial_entry_message(message: str) -> bool:
    raw = (message or "").strip().lower()
    normalized = normalize_financial_text(message)
    return (
        raw in {
            FINANCIAL_MENU_ID,
            FINANCIAL_NEW_INCOME_ID,
            FINANCIAL_NEW_EXPENSE_ID,
            FINANCIAL_SUMMARY_ID,
            FINANCIAL_CORRECT_LAST_ID,
        }
        or normalized in _ENTRY_COMMANDS
        or normalized in _SUMMARY_COMMANDS
        or normalized in _CORRECTION_COMMANDS
        or looks_like_financial_movement(message)
    )


def should_exit_financial_message(message: str) -> bool:
    """Evita que un borrador financiero bloquee otros módulos del bot."""
    raw = (message or "").strip().lower()
    normalized = normalize_financial_text(message)
    if raw.startswith("finance_") or raw == FINANCIAL_MENU_ID:
        return False
    if raw.startswith("menu_"):
        return True
    if raw.startswith(("calendar_", "fund_", "hito_", "unsatisfied_")):
        return True
    if normalized in _EXTERNAL_COMMANDS:
        return True
    if ("?" in message or normalized.startswith(("como ", "que ", "donde ", "cuando "))) and not is_financial_entry_message(message):
        return True
    return False


def should_handle_financial_message(
    message: str,
    session: dict | None = None,
) -> bool:
    raw = (message or "").strip().lower()
    if is_financial_entry_message(message):
        return True
    if raw.startswith("finance_"):
        return True
    if normalize_financial_text(message) in _CANCEL_COMMANDS:
        return True
    return session is not None


def _set_session_type(user_id: str, session: dict, movement_type: str) -> dict:
    draft = dict(session.get("draft") or {})
    draft["movement_type"] = movement_type
    draft["category"] = classify_category(
        draft.get("description") or "",
        movement_type,
        draft.get("description"),
    )
    parsed_draft = FinancialMovementDraft(**draft)
    missing_fields = parsed_draft.required_missing_fields()
    target_movement_id = session.get("target_movement_id")
    if not missing_fields:
        state = "confirming_update" if target_movement_id else "confirming_creation"
        update_financial_session(
            user_id,
            state=state,
            draft=draft,
            missing_fields=[],
        )
        movement = FinancialMovementConfirm(
            **draft,
            target_movement_id=target_movement_id,
        )
        return confirmation_response(
            movement,
            editing=target_movement_id is not None,
        )

    update_financial_session(
        user_id,
        state="waiting_missing_data",
        draft=draft,
        missing_fields=missing_fields,
    )
    return missing_data_response(parsed_draft)


def handle_financial_message(
    user: dict,
    message: str,
    session: dict | None = None,
) -> dict | str:
    user_id = user.get("id")
    if not user_id:
        return "No pude identificar tu perfil. Escribe *menu* e intenta nuevamente."

    raw = (message or "").strip().lower()
    normalized = normalize_financial_text(message)
    if session is not None:
        raw = _TEXT_ACTION_IDS.get(normalized, raw)

    if raw == FINANCIAL_CANCEL_ID or normalized in _CANCEL_COMMANDS:
        clear_financial_session(user_id)
        return financial_menu("Operación cancelada.\n\n")

    if raw == FINANCIAL_MENU_ID or normalized in {
        "mis finanzas", "ingresos y gastos", "movimientos", "mis movimientos",
    }:
        clear_financial_session(user_id)
        return financial_menu()

    if raw == FINANCIAL_NEW_INCOME_ID or normalized in {
        "registrar ingreso", "nuevo ingreso",
    }:
        start_financial_session(
            user_id,
            "waiting_missing_data",
            draft={"movement_type": "income"},
            missing_fields=["amount", "category", "description", "occurred_on"],
        )
        return _new_movement_prompt("income")

    if raw == FINANCIAL_NEW_EXPENSE_ID or normalized in {
        "registrar gasto", "nuevo gasto",
    }:
        start_financial_session(
            user_id,
            "waiting_missing_data",
            draft={"movement_type": "expense"},
            missing_fields=["amount", "category", "description", "occurred_on"],
        )
        return _new_movement_prompt("expense")

    if raw == FINANCIAL_SUMMARY_ID or normalized in _SUMMARY_COMMANDS:
        return _summary_widget(user)

    if raw == FINANCIAL_CORRECT_LAST_ID or normalized in _CORRECTION_COMMANDS:
        return _correction_widget(user_id)

    if looks_like_financial_movement(message):
        return FINANCIAL_PARSE_TASK

    session = session or get_financial_session(user_id)

    if raw in {FINANCIAL_TYPE_INCOME_ID, FINANCIAL_TYPE_EXPENSE_ID}:
        if not session:
            return financial_menu("No había un movimiento pendiente.\n\n")
        movement_type = (
            "income" if raw == FINANCIAL_TYPE_INCOME_ID else "expense"
        )
        return _set_session_type(user_id, session, movement_type)

    if raw == FINANCIAL_LAST_EDIT_ID:
        if not session or not session.get("target_movement_id"):
            return _correction_widget(user_id)
        start_financial_session(
            user_id,
            "waiting_replacement",
            draft={},
            target_movement_id=session["target_movement_id"],
        )
        return {
            "type": "buttons",
            "body": (
                "Escribe nuevamente el movimiento completo con los datos "
                "corregidos.\n\n"
                "Ejemplo: _Hoy gasté $18.000 en harina_."
            ),
            "options": [
                (FINANCIAL_CANCEL_ID, "Cancelar"),
                MENU_BUTTON[0],
            ],
        }

    if raw == FINANCIAL_LAST_DELETE_ID:
        if not session or not session.get("target_movement_id"):
            return _correction_widget(user_id)
        update_financial_session(user_id, state="confirming_delete")
        return {
            "type": "buttons",
            "body": (
                "⚠️ ¿Confirmas que quieres eliminar el último movimiento?\n\n"
                "Dejará de aparecer en tus resúmenes."
            ),
            "options": [
                (FINANCIAL_DELETE_CONFIRM_ID, "Sí, eliminar"),
                (FINANCIAL_DELETE_CANCEL_ID, "No, conservar"),
            ],
        }

    if raw == FINANCIAL_DELETE_CANCEL_ID:
        clear_financial_session(user_id)
        return financial_menu("Conservé el movimiento sin cambios.\n\n")

    if raw == FINANCIAL_DELETE_CONFIRM_ID:
        if (
            not session
            or session.get("state") != "confirming_delete"
            or not session.get("target_movement_id")
        ):
            return financial_menu("No había una eliminación pendiente.\n\n")
        deleted = soft_delete_financial_movement(
            user_id,
            session["target_movement_id"],
        )
        if not deleted:
            return financial_menu("Ese movimiento ya no estaba disponible.\n\n")
        return financial_menu("✅ Movimiento eliminado del resumen.\n\n")

    if raw == FINANCIAL_EDIT_ID:
        target_id = session.get("target_movement_id") if session else None
        start_financial_session(
            user_id,
            "waiting_replacement",
            draft={},
            target_movement_id=target_id,
        )
        return {
            "type": "buttons",
            "body": (
                "Escribe el movimiento completo con los datos correctos.\n\n"
                "Ejemplo: _Hoy vendí $42.000 en empanadas_."
            ),
            "options": [
                (FINANCIAL_CANCEL_ID, "Cancelar"),
                MENU_BUTTON[0],
            ],
        }

    if raw == FINANCIAL_CONFIRM_ID:
        if not session or session.get("state") not in {
            "confirming_creation", "confirming_update",
        }:
            return financial_menu("No había un movimiento listo para confirmar.\n\n")
        try:
            movement = FinancialMovementConfirm(
                **(session.get("draft") or {}),
                target_movement_id=session.get("target_movement_id"),
            )
        except Exception:
            update_financial_session(user_id, state="waiting_missing_data")
            return missing_data_response(
                FinancialMovementDraft(**(session.get("draft") or {}))
            )

        saved = confirm_financial_movement(
            user_id,
            movement.movement_type,
            movement.amount,
            movement.category,
            movement.description,
            movement.occurred_on,
            original_text=movement.original_text,
            target_movement_id=movement.target_movement_id,
        )
        if not saved:
            return "No pude guardar el movimiento. Inténtalo nuevamente más tarde."
        action = "actualizado" if movement.target_movement_id else "registrado"
        return {
            "type": "buttons",
            "body": (
                f"✅ *Movimiento {action}*\n\n"
                f"{_movement_detail(saved)}"
            ),
            "options": [
                (FINANCIAL_SUMMARY_ID, "Ver resumen"),
                (FINANCIAL_CORRECT_LAST_ID, "Corregir último"),
                MENU_BUTTON[0],
            ],
        }

    if session:
        return FINANCIAL_PARSE_TASK

    if normalized in _ENTRY_COMMANDS:
        return financial_menu()

    return financial_menu()
