"""Revisión del resumen mensual: cálculos locales, sin modificar el libro financiero."""

from decimal import Decimal, InvalidOperation


EXPENSE_LABELS = {
    "insumos_mercaderia": "Insumos y mercadería",
    "transporte": "Transporte",
    "arriendo_servicios": "Arriendo y servicios",
    "permisos_tramites": "Permisos y trámites",
    "marketing": "Publicidad",
    "equipamiento": "Equipamiento",
    "otros_gastos": "Otros gastos",
    "sin_desglose": "Sin categoría",
}
INCOME_LABELS = {
    "ventas": "Ventas", "servicios": "Servicios",
    "aportes_capital": "Aportes (no son ventas)",
    "otros_ingresos": "Otros ingresos por revisar",
}


def snapshot_amount(value):
    """PostgREST puede entregar numeric como texto; nunca convertir null en cero."""
    try:
        if value is None or isinstance(value, bool):
            raise ValueError("Falta el importe del resumen financiero")
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number != number.to_integral_value():
            raise ValueError("Importe financiero inválido")
        return int(number)
    except InvalidOperation as error:
        raise ValueError("Importe financiero inválido") from error


def expense_breakdown(financial):
    if financial.get("loaded") is not True:
        raise ValueError("No se cargó el resumen financiero")
    total = snapshot_amount(financial.get("expense_total"))
    result = {}
    for row in financial.get("categories", []):
        if row["movement_type"] != "expense":
            continue
        key = row["category"] if row["category"] in EXPENSE_LABELS else "sin_desglose"
        result[key] = result.get(key, 0) + snapshot_amount(row["total"])
    difference = total - sum(result.values())
    if difference < 0:
        raise ValueError("El detalle de gastos no coincide con su total")
    if difference:
        result["sin_desglose"] = result.get("sin_desglose", 0) + difference
    return result


def review_expenses(answers, financial):
    # A manual total has no invented allocation between rent, supplies, etc.
    if "expense_review" in answers:
        return dict(answers["expense_review"])
    if answers.get("expenses_confirmed"):
        breakdown = answers.get("expenses_breakdown")
        if isinstance(breakdown, dict) and set(breakdown) <= EXPENSE_LABELS.keys():
            return dict(breakdown)
        return {"sin_desglose": answers["expenses_total"]}
    return expense_breakdown(financial)


def invalidate_projection(answers):
    for key in ("representative", "projected_income", "projected_expenses", "projection_income_draft"):
        answers.pop(key, None)


def clear_period_review(answers):
    for key in ("expenses_confirmed", "income_confirmed", "expenses_total", "income_total",
                "expenses_breakdown", "expense_review", "expense_category", "expense_oneoff",
                "expense_oneoff_confirmed", "income_review", "return_to_review"):
        answers.pop(key, None)
    invalidate_projection(answers)
