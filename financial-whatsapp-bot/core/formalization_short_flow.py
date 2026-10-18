"""Recorrido breve: tres respuestas, presupuesto agrupado y detalle opcional.

Los rangos salen del catálogo fijado en el diagnóstico. Las hipótesis se aceptan
en conjunto; no se convierten en hechos del perfil ni autorizaciones legales.
"""
from copy import deepcopy
from datetime import date, timedelta

from core import formalization_cost_guide as guide
from core.formalization_estimates import _slug


VERSION = "short-v1"
GROUPS = {"permits": "Trámites y permisos", "setup": "Arreglos y equipos", "monthly": "Gastos posteriores"}
CORE_CODES = {"C01", "C07", "C08", "C09", "C12", "C13", "C16", "C17", "C21", "C22", "C23", "C24"}


def enabled(context):
    return context["diagnostic"].get("answers", {}).get("estimate_flow") == VERSION


def group(row):
    if row["code"] == "C16":
        return "permits"  # Navigation by concept; annual totals stay separate.
    if row["code"] in {"C12", "C13"}:
        return "setup"
    if row["frequency"] in {"monthly", "annual"} or (row["frequency"] != "once" and row["code"] in {"C17", "C21", "C22", "C23", "C24"}):
        return "monthly"
    return "permits"


def questions(context):
    activity = _slug(context["diagnostic"].get("profile_snapshot", {}).get("rubro")).removeprefix("rubro_")
    if activity == "alimentos":
        first = ("subtype", "¿Preparas alimentos o vendes productos que ya vienen envasados?",
                 [("alimentos_preparacion", "Preparo alimentos"), ("alimentos_envasados", "Vendo envasados"), (None, "Otra actividad")])
    elif activity == "textil":
        first = ("subtype", "¿Confeccionas o reparas prendas, o solamente las vendes?",
                 [("textil_confeccion", "Confecciono/reparo"), ("textil_venta", "Solo vendo"), (None, "Otra actividad")])
    else:
        first = ("subtype", "Tu actividad está fuera del catálogo piloto de alimentos y textil. Podemos mostrar tus finanzas y dejar los costos pendientes.",
                 [(None, "Continuar")])
    return [first,
            ("adaptations", "¿Cómo está el lugar para trabajar? Estimaremos solo arreglos menores; si necesita obras grandes, elige «No estoy seguro».",
             [("none", "Está preparado"), ("minor", "Necesita arreglos"), (None, "No estoy seguro")]),
            ("equipment_level", "¿Ya tienes los equipos principales? No agregaremos compras de equipos que ya posees.",
             [("all", "Tengo todos"), ("some", "Me faltan algunos"), ("most", "Me falta casi todo")])]


def pending_question(context):
    answers = context["diagnostic"]["answers"].get("short_answers", {})
    return next((q for q in questions(context) if q[0] not in answers), None)


def assumptions(context):
    return ("Escenario: un negocio pequeño, una instalación y capital de hasta $10.000.000; "
            "sin obras estructurales, actividades especiales ni rebajas por Microempresa Familiar. "
            "Los aumentos mensuales se suponen fuera de tus gastos actuales; puedes corregirlos. "
            "Si faltan equipos, su monto queda pendiente hasta que lo indiques. "
            "Los pagos iniciales se suponen pendientes, antes de comenzar; no son vencimientos oficiales.")


def _build(context, today):
    d, answers = context["diagnostic"], context["diagnostic"]["answers"]
    brief = answers["short_answers"]
    facts = {"subtype": brief.get("subtype"), "adaptations": brief.get("adaptations"),
             "small_installation": True, "capital_under_10m": True,
             "mef_route": "not_applicable", "new_operating_costs_not_in_baseline": True}
    # These are scenario hypotheses, not confirmed answers about legal eligibility.
    if brief.get("equipment_level") == "all":
        for component in answers["estimate_catalog"]["document"]["components"]:
            facts["equipment_" + component["id"]] = 0
    updated = {**context, "diagnostic": {**d, "answers": {**answers, "estimate_facts": facts}}}
    rows = guide.build_decisions(updated)
    previous = {i["item_key"]: i for i in guide.guide_items(context)}
    items = []
    for row in rows:
        item = guide.make_item(row)
        code = row["code"]
        if item["item_key"] in previous:
            # Own budgets/exclusions survive a repeated calculation.
            continue
        if code not in CORE_CODES:
            item.update(applicability="not_applicable", applicability_reason="Fuera del escenario base; no acredita exención")
            item["metadata"]["outside_short_scope"] = True
        elif code in {"C01", "C07", "C12", "C13", "C24"} and row["status"] == "ready":
            item.update(applicability="applies", applicability_reason="Hipótesis de presupuesto aceptada en conjunto",
                        amount_clp_min=row["amount_clp_min"], amount_clp_max=row["amount_clp_max"],
                        amount_paid_clp=0, amount_state="estimated", unit="CLP")
            if item["frequency"] == "once":
                item["due_date"] = max(today, date.fromisoformat(d["projection_start"]) - timedelta(days=1)).isoformat()
            item["metadata"].update(origin="FinancIAl", assumed_date=item["frequency"] == "once", scenario_accepted=True)
            item["evidence"].update(origin="FinancIAl", acceptance="grouped-v1")
        item["metadata"]["short_scenario"] = True
        items.append(item)
    return rows, items


def text_action(context, text):
    if "?" in text or "¿" in text:
        return None
    key = _slug(text)
    step = context["diagnostic"]["current_step"]
    if step == "short_needs":
        q = pending_question(context)
        if q:
            return next((f"short_choice_{i}" for i, (_, title) in enumerate(q[2]) if _slug(title) == key), None)
    if step == "short_assumptions":
        return {"ver_estimacion": "short_accept", "no_es_mi_caso": "short_reject"}.get(key)
    if step == "short_groups":
        return next(("short_group_" + group_key for group_key, title in GROUPS.items() if _slug(title) == key), None)
    return None


def prompt(context, widget):
    step = context["diagnostic"]["current_step"]
    if step == "short_needs":
        q = pending_question(context)
        if q:
            index = [r[0] for r in questions(context)].index(q[0]) + 1
            return widget(context, f"Tu negocio · {index}/3\n{q[1]}",
                          [(f"short_choice_{i}", title) for i, (_, title) in enumerate(q[2])])
    if step == "short_assumptions":
        return widget(context, "Prepararé una estimación sin preguntarte por cada trámite.\n\n" + assumptions(context)
                      + "\n\n¿Usamos este escenario? Si no corresponde, mostraré tus finanzas con los costos pendientes.",
                      [("short_accept", "Ver estimación"), ("short_reject", "No es mi caso"), ("pause", "Pausar")])
    if step == "short_groups":
        return widget(context, "¿Qué parte quieres ajustar? Solo cambia lo que conoces; lo demás se conserva.",
                      [("short_group_" + key, title) for key, title in GROUPS.items()])
    return None


def transition(context, action, today):
    d = context["diagnostic"]
    a = deepcopy(d["answers"])
    step, items = d["current_step"], None
    if step == "short_needs":
        q = pending_question(context)
        if q:
            if action == "unknown":
                value = None
            elif action.startswith("short_choice_") and action[13:].isdigit() and int(action[13:]) < len(q[2]):
                value = q[2][int(action[13:])][0]
            else:
                return None
            a.setdefault("short_answers", {})[q[0]] = value
        updated = {**context, "diagnostic": {**d, "answers": a}}
        step = "short_needs" if pending_question(updated) else "short_assumptions"
    elif step == "short_assumptions" and action in {"short_accept", "short_reject"}:
        a["short_assumptions_accepted"] = action == "short_accept"
        updated = {**context, "diagnostic": {**d, "answers": a}}
        rows, items = _build(updated, today)
        if action == "short_reject":
            # Preserve user quotes, but do not use hypothetical prices they rejected.
            items = [guide.make_item(row) for row in rows if "estimate:" + row["key"] not in {i["item_key"] for i in context["items"]}]
            for item in items:
                # Rejected assumptions cannot even establish a free/exempt cost.
                item["applicability"] = "to_confirm"
        a.update(estimate_decisions=rows, estimate_scope={key: d.get(key) for key in ("activity_code", "comuna_code", "location_type")},
                 estimate_coverage_confirmed=False, catalog_coverage_confirmed=False, short_assumptions=assumptions(context))
        step = "short_calculate"
    elif step == "short_groups" and action.startswith("short_group_") and action[12:] in GROUPS:
        a.update(estimate_group=action[12:], estimate_page=0)
        step = "estimate_costs"
    else:
        return None
    return {"answers": a, "current_step": step, "pending_field": step, "conversation_status": "active"}, items


def totals(context):
    totals = {key: {"min": 0, "max": 0, "pending": 0} for key in ("permits", "setup", "monthly", "annual")}
    for item in guide.guide_items(context):
        if item["applicability"] == "not_applicable" or item.get("included_in_baseline"):
            continue
        decision = item["metadata"]["decision"]
        bucket = ("annual" if item["frequency"] == "annual" else "monthly" if item["frequency"] == "monthly"
                  else group({**decision, "frequency": item["frequency"]}))
        row = totals[bucket]
        if item["applicability"] != "applies" or item.get("amount_clp_max") is None:
            row["pending"] += 1
            continue
        paid = item.get("amount_paid_clp") or 0
        row["min"] += max(0, item["amount_clp_min"] - paid)
        row["max"] += max(0, item["amount_clp_max"] - paid)
    return totals
