"""Preguntas y partidas del presupuesto guiado. Sin SQL, IA ni tarifas implícitas.

La versión consultada se fija en answers; cada partida conserva la decisión,
las fuentes y el origen dentro de metadata/evidence, admitidos por la RPC v1.
"""
from copy import deepcopy
from datetime import date, timedelta

from core.formalization_estimates import select_cost_estimates, _slug
from schemas.formalization_diagnostics import FormalizationItemInput


YES_NO = [(True, "Sí"), (False, "No"), (None, "No sé")]
FREQUENCIES = {"once": "por única vez", "monthly": "al mes", "annual": "al año"}
SPLIT = {
    "C14": [("monthly", "Arriendo mensual"), ("once", "Garantía y entrada al local")],
    "C18": [("once", "Fabricación del letrero"), ("annual", "Derechos del letrero")],
    "C21": [("monthly", "Apoyo contable mensual"), ("annual", "Apoyo para declaración anual"), ("once", "Ayuda contable inicial")],
    "C22": [("monthly", "Reserva mensual para IVA"), ("monthly", "Anticipos mensuales de impuestos"), ("annual", "Impuesto anual pendiente")],
}


class GuideInputError(ValueError):
    """Solo mensajes constantes y seguros para mostrar en la pregunta actual."""


def is_guided(context):
    return bool(context["diagnostic"].get("answers", {}).get("estimate_catalog"))


def questions(context):
    d = context["diagnostic"]
    a = d["answers"]
    doc = a["estimate_catalog"]["document"]
    facts = a.get("estimate_facts", {})
    activity = _slug(d["profile_snapshot"].get("rubro")).removeprefix("rubro_")
    subtype = facts.get("subtype")
    rows = []
    def add(key, text, options=YES_NO):
        rows.append((key, text, options))
    if activity == "alimentos":
        add("subtype", "¿Preparas alimentos o los compras envasados para vender?",
            [("alimentos_preparacion", "Preparo alimentos"), ("alimentos_envasados", "Vendo envasados"), (None, "Otra actividad")])
    elif activity == "textil":
        add("subtype", "¿Confeccionas o reparas prendas, o solamente las vendes?",
            [("textil_confeccion", "Confecciono/reparo"), ("textil_venta", "Solo vendo"), (None, "Otra actividad")])
    else:
        return rows
    add("small_installation", "¿Se trata de un solo espacio pequeño de trabajo, sin procesos industriales ni actividades especiales?")
    add("capital_under_10m", "¿Estimas que el capital destinado al negocio es de hasta $10.000.000? Considera equipos, mercadería y dinero del negocio; no tus ventas mensuales.")
    if subtype == "alimentos_preparacion" and d.get("location_type") == "vivienda":
        add("mef_route", "Para explorar un presupuesto en tu vivienda, ¿usamos la hipótesis de Microempresa Familiar? La municipalidad debe confirmar si cumples sus condiciones.",
            [("accepted_hypothesis", "Usar esa hipótesis"), ("not_applicable", "Ruta general"), (None, "Revisar después")])
    if subtype == "alimentos_envasados":
        add("needs_cold_storage", "¿Los productos necesitan refrigeración?")
    if subtype == "textil_confeccion":
        add("technical_review", "¿La municipalidad te indicó qué revisión necesita tu taller? Si aún no lo consultaste, elige «No sé».",
            [("report", "Informe sanitario"), ("qualification", "Calificación"), (None, "No sé")])
    add("missing_property_documents", "¿Te faltan documentos del lugar, como certificados o antecedentes para tramitar los permisos?")
    add("adaptations", "¿El lugar necesita arreglos? Puedes escribir «no sé» si aún no lo has revisado.",
        [("minor", "Arreglos pequeños"), ("major", "Trabajos grandes"), ("none", "Está preparado")])
    prefixes = {"alimentos_preparacion": ("EQ_AL_", "EQ_SEGURIDAD"),
                "alimentos_envasados": ("EQ_AL_FRIO", "EQ_VENTA_", "EQ_SEGURIDAD"),
                "textil_confeccion": ("EQ_TX_", "EQ_SEGURIDAD"),
                "textil_venta": ("EQ_VENTA_", "EQ_SEGURIDAD")}.get(subtype, ())
    for component in doc["components"]:
        if component["id"].startswith(prefixes):
            add("equipment_" + component["id"], f"{component.get('title', 'Equipo')}: ¿cuántas unidades te faltan comprar? Escribe 0 si ya tienes lo necesario, un número de 1 a 10, o «no sé». No se suman equipos que ya posees.", None)
    add("new_operating_costs_not_in_baseline", "¿Tendrás gastos adicionales de servicios o limpieza que NO están incluidos en tu presupuesto mensual futuro? No cuentes aquí arriendo, personal, contador ni impuestos.")
    add("municipal_patent_required", "¿Te han informado que debes obtener o pagar una patente para esta actividad y lugar? Si aún no lo verificas, elige «No sé».")
    add("waste_charge_required", "¿Debes pagar aseo municipal por el negocio? Si no conoces el cobro de tu caso, elige «No sé».")
    if facts.get("waste_charge_required") is True:
        add("waste_charge_uncovered", "¿Ese cobro de aseo todavía está fuera de los gastos que ya contamos?")
    return rows


def pending_question(context):
    facts = context["diagnostic"]["answers"].get("estimate_facts", {})
    return next((row for row in questions(context) if row[0] not in facts), None)


def text_action(context, text):
    """Acepta el texto de una opción sin consumir preguntas dirigidas al asesor."""
    if "?" in text or "¿" in text:
        return None
    step = context["diagnostic"]["current_step"]
    key = _slug(text)
    if step == "estimate_needs":
        question = pending_question(context)
        if question and question[2]:
            return next((f"gchoice_{i}" for i, (_, label) in enumerate(question[2]) if _slug(label) == key), None)
        return "gzero" if key == "ya_lo_tengo" else None
    choices = {
        "estimate_offer": {"usar_estimacion": "gaccept", "tengo_otro_monto": "gmanual", "tengo_un_monto": "gmanual", "que_incluye": "gscope", "detalles_u_omitir": "gscope"},
        "estimate_scope": {"volver_al_costo": "gbackcost", "no_lo_necesito": "gexclude"},
        "estimate_origin": {"cotizacion": "gquote", "mi_calculo": "guser", "cobro_informado": "gcharge"},
        "estimate_frequency": {"una_sola_vez": "gonce", "mensual": "gmonthly", "anual": "gannual"},
        "estimate_date": {"usar_fecha_supuesta": "gassume"},
    }.get(step, {})
    if step == "estimate_costs":
        choices = {_slug(row["title"]): "gcost_" + row["key"] for row in context["diagnostic"]["answers"]["estimate_decisions"]}
        choices["calcular_presupuesto"] = "gdone"
    if step.startswith("estimate_") and step not in {"estimate_needs", "estimate_costs", "estimate_coverage"}:
        choices["revisar_despues"] = "gskip"
    return choices.get(key)


def build_decisions(context):
    d = context["diagnostic"]
    a = d["answers"]
    catalog = a["estimate_catalog"]
    doc = catalog["document"]
    facts = dict(a.get("estimate_facts", {}))
    equipment = {key.removeprefix("equipment_"): value for key, value in facts.items() if key.startswith("equipment_")}
    facts["missing_components"] = None if any(value is None for value in equipment.values()) or not equipment else {key: value for key, value in equipment.items() if value}
    profile = {**d["profile_snapshot"], "location_type": d["location_type"]}
    supported = (_slug(profile.get("rubro")).removeprefix("rubro_") in {"alimentos", "textil"}
                 and _slug(profile.get("comuna")) in doc["scope"]["communes"]
                 and d["location_type"] in doc["scope"]["locations"])
    selected = {row["code"]: row for row in select_cost_estimates(doc, profile, facts)["decisions"]} if supported else {}
    rows = []
    for cost in doc["costs"]:
        decision = selected.get(cost["code"], {"code": cost["code"], "title": cost["title"], "status": "pending"})
        if cost["code"] in SPLIT:
            variants = zip(cost["variants"], SPLIT[cost["code"]])
        else:
            chosen = next((v for v in cost["variants"] if v["id"] == decision.get("variant_id")), None)
            # Never choose among alternative permits merely because a range exists.
            if chosen is None and len(cost["variants"]) == 1 and decision.get("status") == "deferred":
                chosen = cost["variants"][0]
            frequency = {"per_request": "once"}.get(cost["frequency"], cost["frequency"])
            variants = [(chosen, (frequency, cost["title"]))]
        for variant, (frequency, title) in variants:
            row = {**decision, "key": cost["code"], "title": title, "frequency": frequency,
                   "variant": variant, "catalog_version": catalog["version"],
                   "catalog_id": catalog["id"], "review_by": catalog.get("review_by"),
                   "source_ids": cost.get("sources", [])}
            if cost["code"] in SPLIT:
                row.update(key=cost["code"] + "_" + variant["id"], status="pending")
            # Supplementary options require explicit acceptance of their scope.
            if supported and variant and variant["method"] in {"fixed", "range"} and variant.get("unit") == "CLP" and row.get("status") in {"pending", "deferred"}:
                if cost["code"] not in {"C07", "C08", "C12", "C13", "C24"}:
                    row.update(status="ready", amount_clp_min=int(variant["min"]), amount_clp_max=int(variant["max"]))
            # Hiring estimates need a specific headcount and contract assumption;
            # until captured, allow a user budget, not a one-person default.
            if cost["code"] == "C23":
                row.update(status="pending", amount_clp_min=None, amount_clp_max=None)
            rows.append(row)
    return rows


def current(context):
    a = context["diagnostic"]["answers"]
    return next(row for row in a["estimate_decisions"] if row["key"] == a["estimate_selected"])


def guide_items(context):
    return [item for item in context["items"] if item["item_key"].startswith("estimate:")]


def make_item(row):
    frequency = row["frequency"] if row["frequency"] in FREQUENCIES else "to_confirm"
    return {"item_key": "estimate:" + row["key"], "label": row["title"],
            "item_kind": "initial_payment" if frequency == "once" else "future_operating",
            "economic_nature": "investment" if row["code"] in {"C12", "C13"} else "tax" if row["code"] == "C22" else "guarantee" if row["key"] == "C14_entrada_contrato" else "expense",
            "cash_direction": "outflow", "applicability": "not_applicable" if row["status"] == "not_applicable" else "to_confirm",
            "frequency": frequency, "source_type": "calculation", "amount_state": "pending",
            "metadata": {"financial_estimate": True, "decision": deepcopy(row), "requires_verification": True},
            "evidence": {"catalog_version": row["catalog_version"], "catalog_id": row["catalog_id"], "source_ids": row["source_ids"]}}


def _range(row, money):
    low, high = row.get("amount_clp_min"), row.get("amount_clp_max")
    return money(low) if low == high else f"{money(low)} a {money(high)}"


def prompt(context, widget, money):
    d = context["diagnostic"]
    a, step = d["answers"], d["current_step"]
    if step == "estimate_needs":
        question = pending_question(context)
        if not question:
            return widget(context, "Ya revisamos las necesidades. Continuemos con el presupuesto.", [("gnext", "Continuar")])
        _, text, options = question
        choices = [(f"gchoice_{i}", title) for i, (_, title) in enumerate(options)] if options else [("gzero", "Ya lo tengo"), ("unknown", "No sé"), ("pause", "Pausar")]
        return widget(context, text, choices)
    if step == "estimate_costs":
        rows = a["estimate_decisions"]
        page = a.get("estimate_page", 0)
        saved = {i["item_key"]: i for i in guide_items(context)}
        choices = []
        for row in rows[page * 7:(page + 1) * 7]:
            item = saved.get("estimate:" + row["key"], {})
            flag = "✓ " if item.get("applicability") == "not_applicable" or item.get("amount_clp_max") is not None else "• "
            choices.append(("gcost_" + row["key"], (flag + row["title"])[:24]))
        if page:
            choices.append(("gprev", "Anteriores"))
        if (page + 1) * 7 < len(rows):
            choices.append(("gnextpage", "Más costos"))
        choices.append(("gdone", "Calcular presupuesto"))
        return widget(context, "Revisa los costos que necesites. Cada uno permite usar una estimación de FinancIAl, ingresar tu monto o dejarlo pendiente. "
                      "Los opcionales no son obligaciones. No repitas costos ya incluidos en otra partida. Puedes calcular un resultado parcial.", choices, listing=True)
    if step == "estimate_scope":
        row = current(context)
        scope = (row.get("variant") or {}).get("includes") or row.get("reason") or "Requiere revisar el caso específico de tu negocio."
        excludes = (row.get("variant") or {}).get("excludes") or "No acredita que este costo sea obligatorio en tu caso."
        return widget(context, f"{row['title']}\n\nIncluye: {scope}\n\nNo incluye: {excludes}\n\n¿Seguimos con este concepto?",
                      [("gbackcost", "Volver al costo"), ("gskip", "Revisar después"), ("gexclude", "No lo necesito")])
    if step == "estimate_offer":
        row = current(context)
        ready = row.get("status") == "ready" and row.get("amount_clp_min") is not None
        suffix = FREQUENCIES.get(row["frequency"], "(periodicidad por confirmar)")
        amount = f"FinancIAl propone {_range(row, money)} {suffix}." if ready else "Aún no tenemos una estimación suficiente para tu caso. Puedes indicar un presupuesto propio o revisarlo después."
        warning = " Es una hipótesis de presupuesto; no un cobro oficial ni un monto garantizado."
        if (row.get("variant") or {}).get("includes"):
            warning += "\nAlcance: " + row["variant"]["includes"]
        if row["code"] == "C24":
            warning += " Aquí se agrega SOLO el aumento que no está en tu proyección mensual."
        saved = next((i for i in guide_items(context) if i["item_key"] == "estimate:" + row["key"]), {})
        if saved.get("amount_clp_max") is not None:
            warning += f"\nGuardado: {money(saved['amount_clp_min'])} a {money(saved['amount_clp_max'])}. Se conserva hasta que completes un cambio."
        choices = ([("gscope", "Qué incluye"), ("gmanual", "Tengo un monto"), ("gskip", "Revisar después")] if not ready else
                   [("gaccept", "Usar estimación"), ("gmanual", "Tengo otro monto"), ("gscope", "Detalles u omitir")])
        return widget(context, f"{row['title']}\n\n{amount}{warning}", choices)
    questions_by_step = {
        "estimate_amount": "¿Cuál es tu presupuesto TOTAL para este concepto? Escribe un monto en pesos. Aún no descuentas lo pagado.",
        "estimate_origin": "¿De dónde viene ese monto? Lo guardaremos como información entregada por ti.",
        "estimate_frequency": "¿Con qué frecuencia se pagaría este monto?",
        "estimate_paid": "¿Cuánto de ese total ya pagaste? Escribe 0 si todavía no has pagado. El efectivo disponible debe ser posterior a esos pagos.",
        "estimate_included": "¿Este monto ya está incluido en los gastos mensuales futuros que confirmaste? Si lo está, no se sumará otra vez.",
        "estimate_date": "¿Cuándo pagarías el saldo? Escribe DD/MM/AAAA. Si no conoces la fecha, podemos suponer el pago al comenzar la proyección; es una hipótesis, no un vencimiento oficial.",
        "estimate_coverage": "¿Revisaste todos los costos relevantes de esta lista? Los que dejaste pendientes seguirán pendientes. Si hay otros costos fuera de la lista, no podremos estimar aún tu capacidad económica completa.",
    }
    if step in questions_by_step:
        options = {
            "estimate_origin": [("gquote", "Cotización"), ("guser", "Mi cálculo"), ("gcharge", "Cobro informado")],
            "estimate_frequency": [("gonce", "Una sola vez"), ("gmonthly", "Mensual"), ("gannual", "Anual")],
            "estimate_included": [("yes", "Sí, ya incluido"), ("no", "No está incluido"), ("unknown", "No sé")],
            "estimate_date": [("gassume", "Usar fecha supuesta"), ("gskip", "Revisar después"), ("pause", "Pausar")],
            "estimate_coverage": [("yes", "Sí, revisé la lista"), ("no", "Hay otros costos"), ("unknown", "No estoy seguro")],
        }.get(step, [("gskip", "Revisar después"), ("pause", "Pausar")])
        return widget(context, questions_by_step[step], options)
    return None


def transition(context, action, raw, parse_amount, today):
    """Retorna patch y lote de partidas; el caller guarda una vez con CAS."""
    d = context["diagnostic"]
    a = deepcopy(d["answers"])
    step = d["current_step"]
    items = None
    def finish(next_step, *, new_items=None):
        return {"current_step": next_step, "pending_field": next_step, "answers": a, "conversation_status": "active"}, new_items
    def next_payment():
        frequency = a["estimate_pending"]["frequency"]
        return "estimate_frequency" if frequency not in FREQUENCIES else "estimate_included" if frequency == "monthly" else "estimate_paid"
    def commit():
        row = current(context)
        pending = a.pop("estimate_pending")
        item = make_item(row)
        item.update({k: v for k, v in pending.items() if k != "origin"})
        item.update(applicability="applies", applicability_reason="Escenario de presupuesto aceptado por el usuario; no acredita obligación legal")
        item["metadata"]["origin"] = pending["origin"]
        item["metadata"]["assumed_date"] = a.pop("estimate_assumed_date", False)
        item["metadata"]["scenario_accepted"] = True
        item["evidence"]["origin"] = pending["origin"]
        item["item_kind"] = "initial_payment" if item["frequency"] == "once" else "future_operating"
        # RES signature alternatives and municipal document overlap need an
        # explicit correction, never silently deleting a user's quote.
        conflicts = {"C05": {"C03", "C04"}, "C03": {"C05"}, "C04": {"C05"}, "C09": {"C10"}, "C10": {"C09"}}
        for saved in guide_items(context):
            code = saved.get("metadata", {}).get("decision", {}).get("code")
            if (code in conflicts.get(row["code"], set()) and saved.get("applicability") == "applies"
                    and saved.get("amount_clp_max", 0) and item.get("amount_clp_max", 0)):
                raise GuideInputError("Ese presupuesto puede repetir una firma o documento ya incluido. Pulsa «Revisar después» para volver a la lista. En la alternativa que no usarás, entra a sus detalles y elige «No lo necesito». Luego retoma este costo.")
        return finish("estimate_costs", new_items=[item])
    if step == "estimate_needs":
        question = pending_question(context)
        if question:
            key, _, options = question
            if action == "unknown":
                value = None
            elif options:
                if action in {"yes", "no"} and options == YES_NO:
                    value = action == "yes"
                elif action.startswith("gchoice_") and action.removeprefix("gchoice_").isdigit() and int(action.removeprefix("gchoice_")) < len(options):
                    value = options[int(action.removeprefix("gchoice_"))][0]
                else:
                    return finish(step)
            else:
                value = 0 if action == "gzero" else parse_amount(raw)
                if value > 10:
                    raise GuideInputError("Escribe una cantidad entre 0 y 10; si el negocio necesita más, deja este equipo pendiente para revisar su presupuesto.")
            a.setdefault("estimate_facts", {})[key] = value
        updated = {**context, "diagnostic": {**d, "answers": a}}
        if pending_question(updated):
            return finish("estimate_needs")
        a["estimate_decisions"] = build_decisions(updated)
        a["estimate_scope"] = {key: d.get(key) for key in ("activity_code", "comuna_code", "location_type")}
        a["estimate_page"] = 0
        return finish("estimate_costs", new_items=[make_item(row) for row in a["estimate_decisions"]])
    if step == "estimate_costs":
        if action in {"gprev", "gnextpage"}:
            max_page = (len(a["estimate_decisions"]) - 1) // 7
            a["estimate_page"] = min(max_page, max(0, a.get("estimate_page", 0) + (1 if action == "gnextpage" else -1)))
        elif action == "gdone":
            return finish("estimate_coverage")
        elif action.startswith("gcost_") and any(row["key"] == action[6:] for row in a["estimate_decisions"]):
            a["estimate_selected"] = action[6:]
            a.pop("estimate_pending", None)
            a.pop("estimate_assumed_date", None)
            return finish("estimate_offer")
        return finish(step)
    if step == "estimate_coverage":
        if action not in {"yes", "no", "unknown"}:
            return finish(step)
        a["estimate_coverage_confirmed"] = action == "yes"
        a["catalog_coverage_confirmed"] = False  # legal verification remains separate
        a.pop("return_to_review", None)
        return finish("review")
    if action in {"gskip", "gexclude"}:
        row = current(context)
        a.pop("estimate_pending", None)
        a.pop("estimate_assumed_date", None)
        if action == "gskip":
            # Abandoning an edit preserves a previous quote, including its source.
            return finish("estimate_costs")
        saved = next((i for i in guide_items(context) if i["item_key"] == "estimate:" + row["key"]), None)
        item = {key: value for key, value in saved.items() if key in FormalizationItemInput.model_fields} if saved else make_item(row)
        item.update(applicability="not_applicable", applicability_reason="Declaración del usuario; no acredita exención")
        return finish("estimate_costs", new_items=[item])
    if action in {"gscope", "gbackcost"}:
        return finish("estimate_scope" if action == "gscope" else "estimate_offer")
    row = current(context)
    if step == "estimate_offer":
        if action == "gmanual":
            return finish("estimate_amount")
        if action == "gaccept" and row.get("status") == "ready":
            a["estimate_pending"] = {"amount_clp_min": row["amount_clp_min"], "amount_clp_max": row["amount_clp_max"],
                                     "unit": "CLP", "amount_state": "estimated", "frequency": row["frequency"],
                                     "origin": "FinancIAl"}
            if row["amount_clp_max"] == 0:
                a["estimate_pending"].update(amount_paid_clp=0, due_date=today.isoformat())
                return commit()
            return finish(next_payment())
    elif step == "estimate_amount":
        amount = parse_amount(raw)
        a["estimate_pending"] = {"amount_clp_min": amount, "amount_clp_max": amount, "unit": "CLP",
                                 "amount_state": "estimated", "frequency": row["frequency"], "origin": "usuario"}
        return finish("estimate_origin")
    elif step == "estimate_origin" and action in {"gquote", "guser", "gcharge"}:
        pending = a["estimate_pending"]
        pending["origin"] = {"gquote": "cotización declarada por usuario", "guser": "cálculo del usuario", "gcharge": "cobro informado por usuario"}[action]
        pending["amount_state"] = "quoted" if action == "gquote" else "estimated"
        return finish(next_payment())
    elif step == "estimate_frequency" and action in {"gonce", "gmonthly", "gannual"}:
        a["estimate_pending"]["frequency"] = action[1:]
        return finish(next_payment())
    elif step == "estimate_paid":
        paid = parse_amount(raw)
        if paid > a["estimate_pending"]["amount_clp_max"]:
            raise GuideInputError("Lo pagado no puede superar el presupuesto. Escribe 0 si aún no has pagado.")
        a["estimate_pending"]["amount_paid_clp"] = paid
        return finish("estimate_date")
    elif step == "estimate_included" and action in {"yes", "no", "unknown"}:
        if action == "unknown":
            return finish(step)
        a["estimate_pending"]["included_in_baseline"] = action == "yes"
        return commit()
    elif step == "estimate_date":
        if action == "gassume":
            when = date.fromisoformat(d["projection_start"])
            if a["estimate_pending"]["frequency"] == "once":
                when = max(today, when - timedelta(days=1))
            a["estimate_assumed_date"] = True
        else:
            pieces = raw.strip().split("/")
            when = date(int(pieces[2]), int(pieces[1]), int(pieces[0])) if len(pieces) == 3 else date.fromisoformat(raw)
            if when < today:
                raise GuideInputError("Indica hoy o una fecha futura en que pagarás el saldo pendiente.")
            a["estimate_assumed_date"] = False
        a["estimate_pending"]["due_date"] = when.isoformat()
        return commit()
    return finish(step)
