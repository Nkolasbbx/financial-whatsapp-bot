"""Persistencia de ingresos, gastos y borradores conversacionales (HdU13).

Este módulo solamente accede a Supabase. La detección de intención, la
clasificación mediante IA y la construcción de mensajes de WhatsApp deben
permanecer en las capas ``core`` y ``services``.
"""

from __future__ import annotations

from datetime import date
from typing import Any


VALID_MOVEMENT_TYPES = {"income", "expense"}
VALID_SESSION_STATES = {
    "waiting_missing_data",
    "confirming_creation",
    "choosing_correction",
    "waiting_replacement",
    "confirming_update",
    "confirming_delete",
}

INCOME_CATEGORIES = {
    "ventas",
    "servicios",
    "aportes_capital",
    "otros_ingresos",
}

EXPENSE_CATEGORIES = {
    "insumos_mercaderia",
    "transporte",
    "arriendo_servicios",
    "permisos_tramites",
    "marketing",
    "equipamiento",
    "otros_gastos",
}

_UNSET = object()


def _admin_client():
    import dependencies

    if dependencies.supabase_admin is None:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY no está configurada; "
            "no se pueden administrar movimientos financieros"
        )
    return dependencies.supabase_admin


def _first_record(data: Any) -> dict | None:
    """Normaliza respuestas RPC que pueden llegar como objeto o lista."""
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data:
        first = data[0]
        return first if isinstance(first, dict) else None
    return None


def _normalize_description(description: str) -> str:
    normalized = (description or "").strip()
    if not normalized:
        raise ValueError("La descripción del movimiento no puede estar vacía")
    if len(normalized) > 500:
        raise ValueError(
            "La descripción del movimiento no puede superar 500 caracteres"
        )
    return normalized


def _normalize_original_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > 2000:
        raise ValueError(
            "El mensaje original no puede superar 2000 caracteres"
        )
    return normalized


def _validate_movement_fields(
    movement_type: str,
    amount: int,
    category: str,
) -> tuple[str, int, str]:
    normalized_type = (movement_type or "").strip().lower()
    if normalized_type not in VALID_MOVEMENT_TYPES:
        raise ValueError(f"Tipo de movimiento inválido: {movement_type}")

    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise ValueError("El monto debe ser un número entero mayor que cero")

    normalized_category = (category or "").strip().lower()
    allowed_categories = (
        INCOME_CATEGORIES
        if normalized_type == "income"
        else EXPENSE_CATEGORIES
    )
    if normalized_category not in allowed_categories:
        raise ValueError(
            f"La categoría '{category}' no corresponde al tipo "
            f"'{normalized_type}'"
        )

    return normalized_type, amount, normalized_category


def _normalize_missing_fields(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for value in values or []:
        field_name = str(value).strip()
        if field_name and field_name not in normalized:
            normalized.append(field_name)
    return normalized


# ============================================================
# SESIONES Y BORRADORES
# ============================================================

def get_financial_session(user_id: str) -> dict | None:
    """Obtiene la sesión financiera activa de un usuario."""
    result = (
        _admin_client()
        .table("financial_movement_sessions")
        .select("*")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def start_financial_session(
    user_id: str,
    state: str,
    *,
    draft: dict | None = None,
    missing_fields: list[str] | None = None,
    target_movement_id: str | None = None,
) -> dict | None:
    """Crea o reemplaza la única sesión financiera del usuario."""
    if state not in VALID_SESSION_STATES:
        raise ValueError(f"Estado de sesión financiera inválido: {state}")
    if draft is not None and not isinstance(draft, dict):
        raise ValueError("El borrador financiero debe ser un objeto")

    payload = {
        "user_id": user_id,
        "state": state,
        "draft": dict(draft or {}),
        "missing_fields": _normalize_missing_fields(missing_fields),
        "target_movement_id": target_movement_id,
    }

    result = (
        _admin_client()
        .table("financial_movement_sessions")
        .upsert(payload, on_conflict="user_id")
        .execute()
    )
    return result.data[0] if result.data else None


def update_financial_session(
    user_id: str,
    *,
    state: str | None = None,
    draft: Any = _UNSET,
    missing_fields: Any = _UNSET,
    target_movement_id: Any = _UNSET,
) -> dict | None:
    """Actualiza solo los campos entregados de una sesión existente."""
    changes: dict[str, Any] = {}

    if state is not None:
        if state not in VALID_SESSION_STATES:
            raise ValueError(
                f"Estado de sesión financiera inválido: {state}"
            )
        changes["state"] = state

    if draft is not _UNSET:
        if not isinstance(draft, dict):
            raise ValueError("El borrador financiero debe ser un objeto")
        changes["draft"] = dict(draft)

    if missing_fields is not _UNSET:
        changes["missing_fields"] = _normalize_missing_fields(
            missing_fields
        )

    if target_movement_id is not _UNSET:
        changes["target_movement_id"] = target_movement_id

    if not changes:
        return get_financial_session(user_id)

    result = (
        _admin_client()
        .table("financial_movement_sessions")
        .update(changes)
        .eq("user_id", user_id)
        .execute()
    )
    return result.data[0] if result.data else None


def clear_financial_session(user_id: str) -> None:
    """Elimina cualquier borrador financiero pendiente del usuario."""
    (
        _admin_client()
        .table("financial_movement_sessions")
        .delete()
        .eq("user_id", user_id)
        .execute()
    )


# ============================================================
# MOVIMIENTOS
# ============================================================

def get_financial_movement(
    user_id: str,
    movement_id: str,
    *,
    include_deleted: bool = False,
) -> dict | None:
    """Obtiene un movimiento verificando que pertenezca al usuario."""
    query = (
        _admin_client()
        .table("financial_movements")
        .select("*")
        .eq("id", movement_id)
        .eq("user_id", user_id)
    )
    if not include_deleted:
        query = query.eq("status", "confirmed")

    result = query.limit(1).execute()
    return result.data[0] if result.data else None


def get_last_financial_movement(user_id: str) -> dict | None:
    """Retorna el último movimiento registrado, no el de fecha más reciente."""
    result = (
        _admin_client()
        .table("financial_movements")
        .select("*")
        .eq("user_id", user_id)
        .eq("status", "confirmed")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def list_financial_movements(
    user_id: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Lista movimientos confirmados en un rango semiabierto de fechas."""
    if limit < 1:
        raise ValueError("El límite debe ser mayor que cero")
    if offset < 0:
        raise ValueError("El offset no puede ser negativo")
    if start_date and end_date and end_date <= start_date:
        raise ValueError("La fecha final debe ser posterior a la inicial")

    query = (
        _admin_client()
        .table("financial_movements")
        .select("*")
        .eq("user_id", user_id)
        .eq("status", "confirmed")
    )
    if start_date is not None:
        query = query.gte("occurred_on", start_date.isoformat())
    if end_date is not None:
        query = query.lt("occurred_on", end_date.isoformat())

    result = (
        query.order("occurred_on", desc=True)
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return result.data or []


def confirm_financial_movement(
    user_id: str,
    movement_type: str,
    amount: int,
    category: str,
    description: str,
    occurred_on: date,
    *,
    original_text: str | None = None,
    target_movement_id: str | None = None,
) -> dict | None:
    """Confirma un movimiento nuevo o reemplaza uno existente mediante RPC.

    La función SQL también elimina la sesión financiera dentro de la misma
    transacción, evitando un movimiento confirmado con un borrador pendiente.
    """
    normalized_type, normalized_amount, normalized_category = (
        _validate_movement_fields(movement_type, amount, category)
    )
    normalized_description = _normalize_description(description)
    normalized_original_text = _normalize_original_text(original_text)

    if not isinstance(occurred_on, date):
        raise ValueError("La fecha del movimiento es obligatoria")

    result = _admin_client().rpc(
        "confirm_financial_movement",
        {
            "p_user_id": user_id,
            "p_movement_type": normalized_type,
            "p_amount": normalized_amount,
            "p_category": normalized_category,
            "p_description": normalized_description,
            "p_occurred_on": occurred_on.isoformat(),
            "p_original_text": normalized_original_text,
            "p_target_movement_id": target_movement_id,
        },
    ).execute()

    return _first_record(result.data)


def soft_delete_financial_movement(
    user_id: str,
    movement_id: str,
) -> dict | None:
    """Elimina lógicamente un movimiento y limpia su sesión mediante RPC."""
    result = _admin_client().rpc(
        "soft_delete_financial_movement",
        {
            "p_user_id": user_id,
            "p_movement_id": movement_id,
        },
    ).execute()
    return _first_record(result.data)


def get_financial_month_summary(
    user_id: str,
    month_start: date,
    month_end: date,
) -> dict:
    """Obtiene totales y categorías para un rango mensual semiabierto."""
    if not isinstance(month_start, date) or not isinstance(month_end, date):
        raise ValueError("Las fechas del resumen deben ser válidas")
    if month_end <= month_start:
        raise ValueError("La fecha final debe ser posterior a la inicial")

    result = _admin_client().rpc(
        "get_financial_month_summary",
        {
            "p_user_id": user_id,
            "p_month_start": month_start.isoformat(),
            "p_month_end": month_end.isoformat(),
        },
    ).execute()

    summary = _first_record(result.data)
    if summary is None:
        raise RuntimeError("Supabase no devolvió el resumen financiero")
    return summary
