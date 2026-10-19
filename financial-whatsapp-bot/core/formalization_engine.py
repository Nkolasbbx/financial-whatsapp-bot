"""HDU017: cálculos puros en CLP. Sin SQL, IA ni tarifas implícitas.

Reglas propuestas del producto, no una certificación tributaria ni crediticia.
Los importes desconocidos bloquean la clasificación, pero permiten presupuestar
la parte conocida. Ningún impuesto se obtiene aplicando una tasa universal.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from schemas.formalization_diagnostics import FormalizationAssessmentInput


RULES_VERSION = "formalization-v1.0-proposal"
RULES_CONFIG = {"horizon_months": 3, "stress_sales_percent": 15, "reserve_months": 1}


def catalog_applicability(concept: dict, facts: dict) -> str:
    """Tres valores: una condición desconocida nunca se interpreta como falsa."""
    conditions = concept.get("conditions") or {}
    if conditions.get("version") != 1 or not isinstance(conditions.get("all"), list):
        return "to_confirm"
    unresolved = False
    for clause in conditions["all"]:
        if clause.get("operator") != "equals" or clause.get("field") not in facts or facts[clause["field"]] is None:
            unresolved = True
        elif facts[clause["field"]] != clause.get("value"):
            return "not_applicable"
    if unresolved or any(facts.get(key) is None for key in conditions.get("requires", [])):
        return "to_confirm"
    return "applies"


def month_after(day: date, offset: int = 1) -> date:
    index = day.year * 12 + day.month - 1 + offset
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))


def convert_clp(amount: Decimal, rate: Decimal, *, inflow: bool = False) -> int:
    """La tasa y su fecha deben venir de una fuente confirmada, nunca de aquí."""
    if not amount.is_finite() or not rate.is_finite() or amount < 0 or rate <= 0:
        raise ValueError("Importe/tasa inválidos")
    return int((amount * rate).to_integral_value(rounding=ROUND_FLOOR if inflow else ROUND_CEILING))


@dataclass(frozen=True)
class CashEvent:
    key: str
    on: date
    amount: int
    direction: str
    business_income: bool = False
    recurring: bool = False

    def __post_init__(self):
        if type(self.amount) is not int or self.amount < 0:
            raise ValueError("Los flujos deben ser CLP enteros no negativos")
        if self.direction not in {"inflow", "outflow"}:
            raise ValueError("Dirección inválida")
        if self.business_income and self.direction != "inflow":
            raise ValueError("Una venta no puede ser un egreso")


def evaluate_projection(cash: int, start: date, events: list[CashEvent]) -> dict:
    """Simula preparación y tres meses. Empates de fecha: pagos primero."""
    if type(cash) is not int or cash < 0 or start.day != 1:
        raise ValueError("Indica efectivo no negativo y el primer día de proyección")
    if len({event.key for event in events}) != len(events):
        raise ValueError("Los flujos no pueden tener claves duplicadas")
    limits = [month_after(start, i) for i in range(4)]
    included = [event for event in events if event.on < limits[-1]]
    ordered = sorted(included, key=lambda event: (event.on, event.direction == "inflow", event.key))
    months = [{"month": limits[i].isoformat(), "inflows": 0, "outflows": 0,
               "stress_inflows": 0, "recurring_outflows": 0} for i in range(3)]
    balance = stress_balance = minimum = stress_minimum = cash
    preparation_in = preparation_out = 0
    first_gap = None
    for event in ordered:
        stress_amount = event.amount * 85 // 100 if event.business_income else event.amount
        sign = 1 if event.direction == "inflow" else -1
        balance += sign * event.amount
        stress_balance += sign * stress_amount
        minimum, stress_minimum = min(minimum, balance), min(stress_minimum, stress_balance)
        if balance < 0 and first_gap is None:
            first_gap = {"on": event.on.isoformat(), "key": event.key, "shortfall": -balance}
        if event.on < start:
            if sign == 1:
                preparation_in += event.amount
            else:
                preparation_out += event.amount
        else:
            index = (event.on.year - start.year) * 12 + event.on.month - start.month
            month = months[index]
            month["inflows" if sign == 1 else "outflows"] += event.amount
            if sign == 1:
                month["stress_inflows"] += stress_amount
            elif event.recurring:
                month["recurring_outflows"] += event.amount
    opening = cash + preparation_in - preparation_out
    closing = stress_closing = opening
    # Preparatory business sales must not be used before authorization. The
    # bridge never generates these; stress opening still accounts for any event.
    stress_closing = cash + sum(
        (1 if e.direction == "inflow" else -1) * (e.amount * 85 // 100 if e.business_income else e.amount)
        for e in included if e.on < start
    )
    for month in months:
        month["net_cashflow"] = month["inflows"] - month["outflows"]
        month["stress_net_cashflow"] = month["stress_inflows"] - month["outflows"]
        closing += month["net_cashflow"]
        stress_closing += month["stress_net_cashflow"]
        month.update(closing_balance=closing, stress_closing_balance=stress_closing)
    reserve = max(month["recurring_outflows"] for month in months)
    net = sum(month["net_cashflow"] for month in months)
    stress_net = sum(month["stress_net_cashflow"] for month in months)
    reasons = []
    if opening < 0:
        reasons.append("INITIAL_CASH_GAP")
    if minimum < 0:
        reasons.append("BASE_PAYMENT_UNCOVERED")
    if net < 0:
        reasons.append("BASE_CASH_BURN")
    if min(opening, minimum) < reserve:
        reasons.append("RESERVE_NOT_COVERED")
    if stress_net < 0:
        reasons.append("STRESS_CASH_BURN")
    if stress_minimum < 0:
        reasons.append("STRESS_PAYMENT_UNCOVERED")
    operating = any(e.amount and e.on >= start and (e.business_income or e.recurring) for e in included)
    if not operating:
        capacity = None
    elif opening < 0 or minimum < 0 or net < 0:
        capacity = "baja"
    elif opening >= reserve and minimum >= reserve and net > 0 and stress_minimum >= 0 and stress_net >= 0:
        capacity = "alta"
    else:
        capacity = "media"
    return {"capacity": capacity, "months": months, "opening_balance": opening,
            "preparation_inflows": preparation_in, "preparation_outflows": preparation_out,
            "minimum_balance": minimum, "stress_minimum_balance": stress_minimum,
            "reserve_target": reserve, "initial_cash_gap": max(0, -opening),
            "maximum_cash_gap": max(0, -minimum), "first_uncovered_payment": first_gap,
            "reasons": reasons, "has_operating_projection": operating}


def _day(value) -> date | None:
    return date.fromisoformat(str(value)) if value else None


def evaluate_diagnostic(context: dict) -> FormalizationAssessmentInput:
    """Construye un escenario prudente con las partidas y respuestas persistidas.

    Una partida futura reemplaza otra, no se suma dos veces. Los importes
    iniciales descuentan abonos históricos, el efectivo ya debe ser posterior
    a esos abonos. Cuotas/fechas explícitas prevalecen sobre el modelo mensual.
    """
    diagnostic, items = context["diagnostic"], context.get("items", [])
    answers = diagnostic.get("answers") or {}
    missing = []
    def require(condition, key):
        if not condition and key not in missing:
            missing.append(key)
    for key in ("activity_code", "comuna_code", "location_type", "period_start", "period_end", "projection_start"):
        require(bool(diagnostic.get(key)), key)
    for key in ("expenses_confirmed", "income_confirmed", "representative", "calendar_confirmed",
                "preparation_confirmed", "owner_confirmed", "debt_confirmed", "catalog_coverage_confirmed"):
        require(answers.get(key) is True, key)
    cash = answers.get("available_cash")
    require(type(cash) is int and cash >= 0, "available_cash")
    require(diagnostic.get("financial_snapshot", {}).get("loaded") is True, "financial_sources")
    require(diagnostic.get("rules_version") == RULES_VERSION, "rules_version")
    require(diagnostic.get("rules_config") == RULES_CONFIG, "rules_config")
    start = _day(diagnostic.get("projection_start"))
    require(start is not None and start.day == 1, "projection_start")
    period_start, period_end = _day(diagnostic.get("period_start")), _day(diagnostic.get("period_end"))
    require(period_start is not None and period_start.day == 1 and period_end == month_after(period_start), "monthly_period")
    by_id = {str(row["id"]): row for row in items if row.get("id")}
    replaced = set()
    for item in items:
        if item.get("applicability") == "applies" and item.get("replaces_item_id"):
            target = str(item["replaces_item_id"])
            require(target in by_id and target not in replaced, "replacement:" + item["item_key"])
            replaced.add(target)
    events, costs, sources = [], [], []
    baseline_income = baseline_expenses = future_income = future_operating = 0
    initial_low = initial_high = 0
    for item in items:
        key = item["item_key"]
        low, high = item.get("amount_clp_min"), item.get("amount_clp_max")
        applied = item.get("applicability")
        cat = item.get("catalog_snapshot") or {}
        meta = item.get("metadata") or {}
        if meta.get("financial_estimate") and applied == "applies":
            require(False, "source_review:" + key)
            catalog = answers.get("estimate_catalog") or {}
            registry = catalog.get("document", {}).get("source_details", {})
            for source_id in (meta.get("decision") or {}).get("source_ids", []):
                source = registry.get(source_id)
                if source and source not in sources:
                    sources.append(source)
        if cat:
            for source in cat.get("source_refs", []):
                if source not in sources:
                    sources.append(source)
        costs.append({"key": key, "label": item["label"], "kind": item["item_kind"],
                      "applicability": applied, "min": low, "max": high,
                      "paid": item.get("amount_paid_clp"), "frequency": item["frequency"],
                      "mandatory_status": cat.get("mandatory_status", "user_declared"),
                      "origin": meta.get("origin"), "due_date": item.get("due_date"),
                      "assumed_date": meta.get("assumed_date", False),
                      "included_in_baseline": item.get("included_in_baseline", False)})
        require(applied != "to_confirm", "applicability:" + key)
        if applied != "applies":
            continue
        known = (type(low) is int and type(high) is int and 0 <= low <= high
                 and item.get("amount_state") != "pending")
        require(known, "amount:" + key)
        if cat and cat.get("pricing_method") != "quote":
            require(cat.get("review_status") == "reviewed", "source_review:" + key)
        require(item.get("metadata", {}).get("ambiguous_scope") is not True, "catalog_scope:" + key)
        if item.get("amount_state") in {"quoted", "estimated"}:
            require(bool(item.get("evidence")), "evidence:" + key)
        if not known:
            continue
        inflow = item["cash_direction"] == "inflow"
        if inflow and item["economic_nature"] in {"capital_contribution", "loan_principal"}:
            committed = item.get("metadata", {}).get("commitment_confirmed") is True
            require(committed, "financing_commitment:" + key)
            if not committed:
                continue
        amount = low if inflow else high
        current = item["item_kind"] == "current_operating"
        if current:
            if inflow and item["economic_nature"] == "revenue":
                baseline_income += amount
            elif not inflow and item["economic_nature"] == "expense":
                baseline_expenses += amount
        if str(item.get("id")) in replaced or (not current and item.get("included_in_baseline")):
            continue
        if inflow and item["economic_nature"] == "revenue" and item["frequency"] == "monthly":
            future_income += amount
        if not inflow and item["economic_nature"] in {"expense", "interest", "tax"} and item["frequency"] == "monthly":
            future_operating += amount
        paid = item.get("amount_paid_clp")
        initial = item["item_kind"] == "initial_payment"
        if initial:
            require(type(paid) is int and paid >= 0, "paid:" + key)
            if type(paid) is int:
                initial_low += max(0, low - paid)
                initial_high += max(0, high - paid)
                amount = max(0, amount - paid)
            else:
                continue
        elif item["frequency"] != "monthly" and type(paid) is int:
            amount = max(0, amount - paid)
        if not start:
            continue
        schedule = item.get("schedule") or []
        dates = []
        if schedule:
            # Schedules contain outstanding payments, not gross historical cost.
            valid = all(isinstance(s, dict) and type(s.get("amount_clp")) is int
                        and s["amount_clp"] >= 0 and s.get("on") for s in schedule)
            require(valid, "schedule:" + key)
            if valid:
                try:
                    dates = [(_day(s["on"]), s["amount_clp"]) for s in schedule]
                except ValueError:
                    require(False, "schedule:" + key)
                if initial or item["frequency"] in {"once", "annual", "per_request"}:
                    require(sum(value for _, value in dates) == amount, "schedule_total:" + key)
                if item["frequency"] == "monthly":
                    for offset in range(3):
                        begin, end = month_after(start, offset), month_after(start, offset + 1)
                        require(sum(value for day, value in dates if begin <= day < end) == amount, "schedule_total:" + key)
        elif item["frequency"] == "monthly":
            for offset in range(3):
                day = month_after(start, offset)
                # Explicit conservative assumption: pay at opening, collect at close.
                if inflow:
                    day = month_after(day) - timedelta(days=1)
                dates.append((day, amount))
        elif item.get("due_date"):
            dates = [(_day(item["due_date"]), amount)]
        else:
            require(amount == 0, "schedule:" + key)
        for index, (day, value) in enumerate(dates):
            if answers.get("cash_as_of") and day < _day(answers["cash_as_of"]) and value:
                require(False, "schedule:" + key)
            events.append(CashEvent(f"{key}:{index}", day, value, item["cash_direction"],
                                    item["economic_nature"] == "revenue" and inflow,
                                    item["frequency"] == "monthly" and item["economic_nature"] not in {"investment", "guarantee"}))
    result = {"rules_version": RULES_VERSION, "rules_config": dict(RULES_CONFIG),
              "period_start": diagnostic.get("period_start"), "period_end": diagnostic.get("period_end"),
              "projection_start": diagnostic.get("projection_start"), "currency": "CLP",
              "current_income": baseline_income, "current_expenses": baseline_expenses,
              "current_operating_result": baseline_income - baseline_expenses,
              "future_known_operating_expenses": future_operating,
              "future_known_income": future_income,
              "future_known_operating_result": future_income - future_operating,
              "initial_remaining_min": initial_low, "initial_remaining_max": initial_high,
              "costs": costs, "sources": sources,
              "assumptions": ["Tres meses; ventas -15% en resistencia; pagos constantes.",
                              "Rangos: mayor egreso y menor ingreso. Reserva no es otro gasto.",
                              "Sin calendario explícito: pagos al inicio y cobros al cierre de mes.",
                              "Presupuesto conocido; las partidas pendientes no valen cero."],
              "disclaimer": "Reglas propuestas de FinancIAl. No certifica permisos, impuestos ni sostenibilidad del hogar."}
    projection = None
    if start and start.day == 1 and type(cash) is int and cash >= 0:
        projection = evaluate_projection(cash, start, events)
        result["projection"] = projection
    if answers.get("estimate_catalog"):
        catalog = answers["estimate_catalog"]
        review_by = _day(catalog.get("review_by"))
        calculated_on = _day(answers.get("budget_as_of"))
        if not calculated_on or (review_by and review_by < calculated_on):
            require(False, "estimate_catalog_expired")
        require(answers.get("estimate_coverage_confirmed") is True, "estimate_coverage")
        require(bool(answers.get("estimate_decisions")), "estimate_needs")
        require(answers.get("estimate_scope") == {key: diagnostic.get(key) for key in ("activity_code", "comuna_code", "location_type")}, "estimate_profile_changed")
        result["estimate_version"] = catalog.get("version")
        result["economic_assessment"] = {
            "capacity": None, "label": "Estimación orientativa de FinancIAl",
            "period_months": 3, "legal_validation": False,
            "scope": "Solo el escenario y los costos revisados; los rangos no son límites garantizados.",
        }
        blockers = [key for key in missing if key != "catalog_coverage_confirmed" and not key.startswith("source_review:")]
        if not blockers and projection and projection.get("has_operating_projection"):
            result["economic_assessment"]["capacity"] = projection["capacity"]
        result["economic_assessment"]["missing_fields"] = blockers
        # A completed short-flow budget is an economic assessment of the user's
        # declared scenario. Municipal/tax verification remains separately recorded.
        if answers.get("estimate_flow") == "short-v1":
            result["verification_pending"] = [key for key in missing if key not in blockers]
            missing = blockers
            result["economic_assessment"]["status"] = (
                "partial" if blockers else "complete" if result["economic_assessment"]["capacity"] else "not_evaluable")
            result["economic_assessment"]["coverage_confirmed"] = answers.get("estimate_coverage_confirmed") is True
        result["known_initial_cash_gap"] = max(0, initial_high - cash) if type(cash) is int else None
        result["future_after_withdrawals_and_debt"] = (future_income - future_operating - answers["owner_amount"] - answers["debt_amount"]
            if type(answers.get("owner_amount")) is int and type(answers.get("debt_amount")) is int else None)
    if missing:
        result["reasons"] = ["MISSING_REQUIRED_COST" if any(":" in key for key in missing) else "PERIOD_NOT_CONFIRMED"]
        # Never expose a seemingly definitive category inside a partial result.
        if projection:
            projection["capacity"] = None
        return FormalizationAssessmentInput(assessment_status="partial", result=result, missing_fields=missing)
    if not projection or not projection["has_operating_projection"]:
        result["reasons"] = ["no_operating_projection"]
        return FormalizationAssessmentInput(assessment_status="not_evaluable", result=result)
    return FormalizationAssessmentInput(assessment_status="complete", capacity=projection["capacity"], result=result)
