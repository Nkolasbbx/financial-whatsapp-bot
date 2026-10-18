"""Edición de partidas, no de movimientos, con CAS y revisiones existentes."""
from copy import deepcopy
from datetime import date, datetime
from zoneinfo import ZoneInfo

from core import formalization_cost_guide as guide
from core import formalization_short_flow as brief
from core.formalization_results import calculate_assessment
from db.formalization_diagnostics import (
    get_formalization_context, save_formalization_draft, confirm_formalization_revision,
    FormalizationConflictError, FormalizationNotFoundError,
)
from schemas.formalization_diagnostics import FormalizationItemInput


class RecalculationPending(RuntimeError):
    """La primera transacción se guardó; falta confirmar la revisión."""


def today():
    return datetime.now(ZoneInfo("America/Santiago")).date()


def editable(context):
    d = context.get("diagnostic") or {}
    return bool(d.get("answers", {}).get("estimate_decisions") and
                d.get("workflow_status") in {"draft", "ready", "confirmed"} and
                d.get("current_step") in {"result", "short_calculate", "portal_costs", "edit"})


def view(context):
    d = context.get("diagnostic")
    if not d or not editable(context):
        return {"available": False, "message": "Primero completa o retoma tu diagnóstico por WhatsApp. Al llegar al resultado podrás ajustar aquí sus costos."}
    answers = d["answers"]
    result = d.get("result") or calculate_assessment(context).result
    rows = []
    for item in guide.guide_items(context):
        decision = item.get("metadata", {}).get("decision", {})
        if decision.get("code") not in brief.CORE_CODES:
            continue
        mode = ("excluded" if item["applicability"] == "not_applicable" else "pending"
                if item["applicability"] == "to_confirm" or item.get("amount_clp_max") is None else
                "estimate" if item.get("metadata", {}).get("origin") == "FinancIAl" else "manual")
        rows.append({"key": item["item_key"], "title": item["label"], "group": brief.group(decision),
                     "mode": mode, "min": item.get("amount_clp_min"), "max": item.get("amount_clp_max"),
                     "paid": item.get("amount_paid_clp") or 0, "frequency": item["frequency"],
                     "due_date": item.get("due_date"), "included": item.get("included_in_baseline", False),
                     "origin": item.get("metadata", {}).get("origin"),
                     "estimate": {"min": decision.get("amount_clp_min"), "max": decision.get("amount_clp_max"),
                                  "frequency": decision.get("frequency")} if decision.get("status") == "ready" else None,
                     "scope": (decision.get("variant") or {}).get("includes") or decision.get("reason") or "Revisa si corresponde a tu negocio."})
    return {"available": True, "diagnostic_id": d["id"], "lock_version": d["lock_version"],
            "period": str(d.get("period_start", ""))[:7], "projection_start": d.get("projection_start"),
            "recalculation_pending": d["current_step"] == "portal_costs", "items": rows,
            "summary": {key: result.get(key) for key in ("current_operating_result", "initial_remaining_min", "initial_remaining_max",
                        "future_after_withdrawals_and_debt", "known_initial_cash_gap")},
            "missing_count": len(d.get("missing_fields") or []),
            "assumptions": result.get("assumptions", []), "sources": result.get("sources", []),
            "catalog_version": answers["estimate_catalog"]["version"]}


def update(user_id, payload):
    context = get_formalization_context(user_id)
    d = context.get("diagnostic")
    if not d or d["id"] != str(payload.diagnostic_id):
        raise FormalizationNotFoundError()
    if d["lock_version"] != payload.lock_version or not editable(context):
        raise FormalizationConflictError()
    existing = {item["item_key"]: item for item in guide.guide_items(context)}
    changed = []
    for change in payload.changes:
        old = existing.get(change.key)
        decision = (old or {}).get("metadata", {}).get("decision", {})
        if not old or decision.get("code") not in brief.CORE_CODES:
            raise ValueError("Solo puedes ajustar los costos mostrados en este diagnóstico.")
        item = {key: deepcopy(value) for key, value in old.items() if key in FormalizationItemInput.model_fields}
        if change.mode in {"pending", "excluded"}:
            item.update(applicability="to_confirm" if change.mode == "pending" else "not_applicable",
                        applicability_reason="Selección del usuario en el panel; no acredita exención")
            if change.mode == "pending":
                item.update(amount_clp_min=None, amount_clp_max=None, amount_state="pending", amount_paid_clp=None)
        else:
            if change.mode == "estimate":
                review = d["answers"]["estimate_catalog"].get("review_by")
                if decision.get("status") != "ready" or not review or date.fromisoformat(str(review)) < today():
                    raise ValueError("No hay una estimación vigente para ese costo. Ingresa un monto propio o déjalo pendiente.")
                low, high, frequency = decision["amount_clp_min"], decision["amount_clp_max"], decision["frequency"]
                origin = "FinancIAl"
            else:
                low = high = change.amount
                frequency = change.frequency
                origin = {"informed": "cobro informado por usuario", "quote": "cotización declarada por usuario", "own": "cálculo del usuario"}[change.origin]
            if frequency not in guide.FREQUENCIES or change.paid > high:
                raise ValueError("Revisa la frecuencia y el monto ya pagado.")
            if frequency == "monthly" and change.paid:
                raise ValueError("Para un gasto mensual indica el total habitual, sin abonos.")
            if frequency != "monthly" and change.included:
                raise ValueError("Solo un gasto mensual puede marcarse como incluido en los gastos habituales.")
            if frequency != "monthly" and high > change.paid and (not change.due_date or change.due_date < today()):
                raise ValueError("Indica hoy o una fecha futura para el saldo pendiente.")
            item.update(applicability="applies", amount_clp_min=low, amount_clp_max=high,
                        amount_paid_clp=change.paid, amount_state="quoted" if change.mode == "manual" and change.origin == "quote" else "estimated",
                        unit="CLP", frequency=frequency, due_date=change.due_date.isoformat() if change.due_date and frequency != "monthly" else None,
                        included_in_baseline=change.included, schedule=[],
                        item_kind="initial_payment" if frequency == "once" else "future_operating")
            item["metadata"].update(origin=origin, assumed_date=False, scenario_accepted=True)
            item["evidence"].update(origin=origin, channel="portal")
        changed.append(FormalizationItemInput.model_validate(item).model_dump(mode="json"))
    # Only changed rows are written; profile, financial movements and other costs remain untouched.
    answers = {**d["answers"], "budget_as_of": today().isoformat()}
    saved = save_formalization_draft(user_id, d["id"], d["lock_version"],
        {"answers": answers, "conversation_status": "paused", "current_step": "portal_costs", "pending_field": None},
        items=changed, reopen=d["workflow_status"] == "confirmed")
    try:
        assessment = calculate_assessment(saved)
        confirmed = confirm_formalization_revision(user_id, d["id"], saved["diagnostic"]["lock_version"], str(payload.request_id), assessment)
        if confirmed.get("replayed"):
            return view(get_formalization_context(user_id))
        return view({**saved, "diagnostic": confirmed["diagnostic"]})
    except FormalizationConflictError:
        raise
    except Exception as error:
        raise RecalculationPending() from error
