"""Resultados legibles y paginados del presupuesto de FinancIAl."""
from core.formalization_cost_guide import FREQUENCIES

ORIENTATION_NOTICE = (
    "🧭 *¿Cómo interpretar tu resultado?*\n\n"
    "La capacidad alta, media o baja es una estimación orientativa basada en tus ingresos, gastos, costos y fechas de pago.\n\n"
    "No es una verdad absoluta ni garantiza lo que ocurrirá con tu negocio. Puede cambiar si cambian tus datos o las condiciones consideradas.\n\n"
    "🤝 Para profundizar, revisa este diagnóstico con un contador, asesor de emprendimiento o profesional del área correspondiente. "
    "Puedes mostrarle los cálculos y supuestos utilizados."
)
BRIEF_ORIENTATION_NOTICE = (
    "🧭 Estimación orientativa, no una verdad absoluta. Depende de tus datos y supuestos. "
    "🤝 Para profundizar, consulta a un contador o asesor de emprendimiento."
)

REASON_LABELS = {
    "INITIAL_CASH_GAP": "El efectivo no alcanza para los pagos previos al inicio.",
    "BASE_PAYMENT_UNCOVERED": "Hay fechas de pago en que el dinero disponible no alcanza.",
    "BASE_CASH_BURN": "En los tres meses, los pagos superan los cobros previstos.",
    "RESERVE_NOT_COVERED": "No se mantiene una reserva equivalente a un mes de pagos habituales.",
    "STRESS_CASH_BURN": "Si las ventas bajan un 15%, los pagos superarían los cobros.",
    "STRESS_PAYMENT_UNCOVERED": "Si las ventas bajan un 15%, habría pagos sin dinero suficiente.",
}


def pending_labels(keys, context):
    names = {item["item_key"]: item["label"] for item in context.get("items", [])}
    fields = {
        "estimate_coverage": "Confirma al final del panel que terminaste de revisar los costos.",
        "expenses_confirmed": "Confirma tus gastos por WhatsApp.",
        "income_confirmed": "Confirma tus ingresos por WhatsApp.",
        "representative": "Confirma por WhatsApp los ingresos y gastos de los meses futuros.",
        "calendar_confirmed": "Confirma por WhatsApp el calendario de cobros y pagos.",
        "preparation_confirmed": "Indica por WhatsApp los otros pagos antes de comenzar (0 si no tienes).",
        "owner_confirmed": "Confirma por WhatsApp tu retiro personal mensual.",
        "debt_confirmed": "Confirma por WhatsApp las cuotas de tus deudas.",
        "available_cash": "Indica por WhatsApp el efectivo disponible para tu negocio.",
        "financial_sources": "Vuelve a consultar los movimientos del mes por WhatsApp.",
        "estimate_catalog_expired": "Es necesario revisar la vigencia de las estimaciones del catálogo.",
        "estimate_profile_changed": "Revisa las estimaciones después del cambio de rubro, comuna o lugar.",
        "estimate_needs": "Completa las preguntas sobre tu negocio por WhatsApp.",
        "projection_start": "Revisa el mes de inicio de la proyección por WhatsApp.",
        "monthly_period": "Revisa el mes observado por WhatsApp.",
    }
    prefixes = {"applicability": "Completa el monto o marca que no corresponde",
                "amount": "Completa el monto", "paid": "Indica lo ya pagado (0 si nada)",
                "schedule": "Revisa la fecha de pago", "schedule_total": "Revisa las cuotas y su total",
                "replacement": "Revisa el gasto que reemplaza", "evidence": "Indica el origen del monto",
                "catalog_scope": "Revisa el alcance del costo", "financing_commitment": "Confirma el financiamiento"}
    labels = []
    for key in keys:
        prefix, _, item_key = key.partition(":")
        label = (f"{prefixes[prefix]}: {names.get(item_key, 'costo por revisar')}." if prefix in prefixes
                 else fields.get(key, "Revisa los datos del diagnóstico por WhatsApp."))
        if label not in labels:
            labels.append(label)
    return labels


def capacity_explanation(result):
    capacity = (result.get("economic_assessment") or {}).get("capacity")
    if not capacity:
        return "Completa los datos pendientes para evaluar el escenario." if (result.get("economic_assessment") or {}).get("missing_fields") else "No hay actividad mensual suficiente para clasificar la capacidad económica."
    reasons = [REASON_LABELS[key] for key in (result.get("projection") or {}).get("reasons", []) if key in REASON_LABELS]
    if reasons:
        return " ".join(reasons[:2])
    return ("El escenario cubre los pagos, mantiene una reserva y resiste una baja de ventas del 15%." if capacity == "alta"
            else "El escenario cubre los pagos, pero tiene un margen de seguridad limitado.")


def capacity_breakdown(result):
    """Explica las mismas reglas del motor con sus importes, sin recalificar."""
    capacity = (result.get("economic_assessment") or {}).get("capacity")
    if capacity not in {"alta", "media", "baja"}:
        return []
    projection = result.get("projection") or {}
    months = projection.get("months") or []
    money = lambda value: "por confirmar" if value is None else "$" + f"{value:,}".replace(",", ".")
    indicator = {"alta": "🟢", "media": "🟡", "baja": "🔴"}[capacity]
    descriptions = {
        "alta": "El escenario cubre el arranque y los pagos, conserva una reserva y sigue cubriendo los pagos si las ventas bajan un 15%.",
        "media": "El escenario cubre el arranque y sus pagos habituales, pero el margen de seguridad es limitado. Abajo puedes ver qué condición falta para alcanzar un nivel alto.",
        "baja": "El escenario muestra un faltante de dinero en alguna fecha o pagos mayores que los cobros previstos. Un buen resultado de ventas por sí solo no asegura poder pagar a tiempo.",
    }
    blocks = [f"{indicator} *¿Por qué tu capacidad es {capacity.upper()}?*\n\n{descriptions[capacity]}"]
    low, high = result.get("initial_remaining_min"), result.get("initial_remaining_max")
    cost = money(high) if low == high else f"{money(low)} a {money(high)}"
    blocks.append(f"🚀 *1. Dinero para comenzar*\n\n"
                  f"• Pagos iniciales pendientes: {cost}.\n"
                  f"• Saldo al iniciar la proyección, después de los cobros y pagos previos: {money(projection.get('opening_balance'))}.\n"
                  f"• Faltante al comenzar: {money(projection.get('initial_cash_gap'))}.")
    net = None
    if months:
        income = sum(month.get("inflows", 0) for month in months)
        outflow = sum(month.get("outflows", 0) for month in months)
        net = income - outflow
        blocks.append(f"🗓️ *2. Cobros y pagos de los tres meses*\n\n"
                      f"• Cobros previstos: {money(income)}.\n• Pagos previstos: {money(outflow)}.\n"
                      f"• Diferencia del periodo: {money(net)}.\n\n"
                      "Incluimos gastos, retiros personales, deudas y pagos puntuales que vencen dentro del periodo. Los pagos anteriores se muestran en el arranque.\n\n"
                      "Para los movimientos mensuales suponemos pagos al inicio y cobros al cierre, salvo calendarios explícitos registrados.")
    minimum = projection.get("minimum_balance")
    opening = projection.get("opening_balance")
    reserve = projection.get("reserve_target")
    reserve_gap = max(0, reserve - min(opening, minimum)) if all(type(value) is int for value in (reserve, opening, minimum)) else None
    blocks.append(f"🛟 *3. Dinero de respaldo*\n\n"
                  f"• Reserva objetivo: {money(reserve)}; equivale al mayor mes de pagos habituales.\n"
                  f"• Menor saldo de todo el escenario: {money(minimum)}.\n"
                  f"• Diferencia para mantener esa reserva: {money(reserve_gap)}.\n\n"
                  "La reserva es dinero que buscamos conservar, no otro gasto que se sume a tus pagos.")
    if months:
        stress_net = sum(month.get("stress_inflows", 0) - month.get("outflows", 0) for month in months)
        blocks.append(f"📉 *4. Si vendes un 15% menos*\n\n"
                      f"Manteniendo los mismos pagos:\n• Diferencia de cobros y pagos de los tres meses: {money(stress_net)}.\n"
                      f"• Menor saldo disponible: {money(projection.get('stress_minimum_balance'))}.\n\n"
                      "Un saldo negativo señala dinero que faltaría para cubrir pagos. Esta simulación considera una caída de ventas, no una predicción de lo que ocurrirá.")
    reasons = projection.get("reasons", [])
    factors = []
    if "INITIAL_CASH_GAP" in reasons:
        factors.append(f"🔴 Al comenzar faltan {money(projection.get('initial_cash_gap'))} para cubrir los pagos previos.")
    if "BASE_PAYMENT_UNCOVERED" in reasons:
        first = projection.get("first_uncovered_payment") or {}
        when = first.get("on")
        if when:
            parts = str(when)[:10].split("-")
            when = "/".join(reversed(parts))
        factors.append(f"🔴 Hay pagos sin cobertura{' el ' + when if when else ''}: "
                       f"el mayor faltante del escenario es {money(projection.get('maximum_cash_gap'))}. "
                       "Esto puede pasar aunque los ingresos totales sean suficientes, si llegan después del pago.")
    if "BASE_CASH_BURN" in reasons and net is not None:
        factors.append(f"🔴 En los tres meses, los pagos superan los cobros en {money(max(0, -net))}; el negocio consume dinero disponible.")
    if "RESERVE_NOT_COVERED" in reasons:
        factors.append(f"🟡 No se conserva la reserva objetivo: faltan {money(reserve_gap)} en el momento de menor disponibilidad.")
    if "STRESS_CASH_BURN" in reasons:
        factors.append("🟡 Con ventas 15% menores, los pagos del periodo superarían los cobros.")
    if "STRESS_PAYMENT_UNCOVERED" in reasons:
        factors.append(f"🟡 Con esa caída de ventas habría pagos sin cobertura; el menor saldo sería {money(projection.get('stress_minimum_balance'))}.")
    if capacity == "media" and net == 0:
        factors.append("🟡 Los cobros y pagos del periodo quedan iguales. El nivel alto requiere un resultado de caja positivo, además de reserva y resistencia a menores ventas.")
    if capacity == "alta":
        factors = ["✅ Cubres el arranque y no hay pagos sin cobertura.",
                   "✅ Mantienes al menos un mes de pagos habituales como reserva.",
                   "✅ Los cobros superan los pagos y resistes ventas 15% menores sin faltantes."]
    if factors:
        blocks.append("🔎 *Qué determinó tu nivel*\n\n" + "\n\n".join(factors))
    blocks.append("✏️ *Puedes actualizar el escenario*\n\n"
                  "Si cambian tus ingresos, gastos, retiros o deudas, actualízalos por WhatsApp. "
                  "Los costos y fechas de formalización se ajustan en el panel. Al guardar, recalculamos tu nivel.\n\n"
                  "La evaluación usa tres meses y los costos altos del rango. Es orientativa y depende de tus datos y las fechas de cobro y pago.")
    blocks.append(ORIENTATION_NOTICE)
    return blocks


def capacity_detail(context, widget, *, page=0):
    result = context["diagnostic"].get("result") or {}
    # Keep each explanation section together whenever it fits on one message.
    content, current = [], ""
    blocks = capacity_breakdown(result)
    for block in blocks:
        parts = [block] if len(block) <= 760 else block.split("\n\n")
        for part in parts:
            pieces = [part] if len(part) <= 760 else pages([part])
            for piece in pieces:
                candidate = (current + "\n\n" + piece).strip()
                if len(candidate) > 760:
                    content.append(current)
                    current = piece
                else:
                    current = candidate
    if current:
        content.append(current)
    content = content or ["📝 Completa los datos pendientes para conocer tu capacidad económica."]
    page = min(max(0, page), len(content) - 1)
    options = []
    if page:
        options.append(("gwhy_" + str(page - 1), "Anterior"))
    if page + 1 < len(content):
        options.append(("gwhy_" + str(page + 1), "Siguiente"))
    options.append(("gsummary", "Volver al resumen"))
    return widget(context, f"💬 *Tu resultado explicado* · {page + 1}/{len(content)}\n\n{content[page]}", options)


def summary(context, widget, money):
    d = context["diagnostic"]
    result = d.get("result") or {}
    if result.get("short_budget"):
        return short_summary(context, widget, money)
    economic = result.get("economic_assessment") or {}
    capacity = economic.get("capacity")
    status = f"Capacidad económica orientativa: {capacity}." if capacity else "Aún faltan datos para estimar tu capacidad económica."
    missing = economic.get("missing_fields", [])
    if not capacity and not missing and not (result.get("projection") or {}).get("has_operating_projection"):
        status = "El escenario quedó sin actividad mensual: no podemos clasificar tu capacidad con ingresos y pagos mensuales en cero."
    after = result.get("future_after_withdrawals_and_debt")
    advice = "Revisa primero los costos pendientes para completar el presupuesto." if missing else (
        "Revisa los gastos o el dinero adicional que necesitarías para cubrir los pagos." if (result.get("projection") or {}).get("maximum_cash_gap", 0) else
        "Verifica los presupuestos y las fechas antes de comprometer pagos.")
    return widget(context, "📊 Estimación de FinancIAl\n" + status + "\n\n"
                  f"Mes observado, ingresos menos gastos: {money(result.get('current_operating_result'))}\n"
                  f"Pagos iniciales pendientes conocidos: {money(result.get('initial_remaining_min'))} a {money(result.get('initial_remaining_max'))}\n"
                  f"Faltante frente a tu efectivo para esos pagos: {money(result.get('known_initial_cash_gap'))}\n"
                  f"Resultado mensual futuro, antes de pagos anuales: {money(result.get('future_known_operating_result'))}\n"
                  f"Después de retiros personales y deudas: {money(after)}\n\n"
                  + advice + "\nHorizonte: tres meses. Los importes pendientes no están incluidos. "
                  + BRIEF_ORIENTATION_NOTICE,
                  [("details", "Ver costos y pasos"), ("sources", "Fuentes y supuestos"), ("edit", "Cambiar datos")])


def short_summary(context, widget, money):
    result = context["diagnostic"]["result"]
    groups = result["short_budget"]
    economic = result.get("economic_assessment") or {}
    capacity = economic.get("capacity")
    missing = pending_labels(economic.get("missing_fields", []), context)
    if capacity:
        indicator = {"alta": "🟢", "media": "🟡", "baja": "🔴"}[capacity]
        status = f"{indicator} Capacidad económica orientativa: {capacity.upper()}\n\n💬 {capacity_explanation(result)}"
    elif missing:
        status = "📝 Para terminar: " + missing[0] + (" Hay más pendientes en «Ver detalle»." if len(missing) > 1 else "")
    else:
        status = "💬 " + capacity_explanation(result)
    pending_notice = (
        "\n\n✏️ Los costos sin una estimación precisa debes completarlos en «Ajustar en mi panel», "
        "con un monto informado. Quedan fuera del total; no son gratuitos."
        if any(row["pending"] for row in groups.values()) else ""
    )

    def amount(key):
        row = groups[key]
        value = money(row["min"]) if row["min"] == row["max"] else f"{money(row['min'])}–{money(row['max'])}"
        if row["pending"] and row["max"] == 0:
            return "por completar"
        return value + (" + pendientes" if row["pending"] else "")

    return widget(context, "📊 *Tu diagnóstico económico*\n\n" + status + "\n\n"
                  f"💰 *Resultado del mes observado*\n{money(result.get('current_operating_result'))}\n\n"
                  f"🚀 *Para comenzar* (saldos pendientes)\n• Trámites y permisos: {amount('permits')}\n"
                  f"• Arreglos y equipos: {amount('setup')}\n"
                  f"Total inicial, incluida preparación: {money(result.get('initial_remaining_min'))}–{money(result.get('initial_remaining_max'))}\n"
                  f"Faltaría cubrir con efectivo: {money(result.get('known_initial_cash_gap'))}\n\n"
                  f"🗓️ *Gastos posteriores*\n• Adicionales al mes: {amount('monthly')}\n• Pagos anuales separados: {amount('annual')}\n\n"
                  f"💵 *Saldo mensual tras gastos, retiros y deudas*\n{money(result.get('future_after_withdrawals_and_debt'))}\n\n"
                  "ℹ️ Proyección: 3 meses, usando los costos altos del rango.\n\n" + BRIEF_ORIENTATION_NOTICE + pending_notice,
                  [("details", "Ver detalle"), ("short_adjust", "Ajustar en mi panel"), ("edit", "Cambiar mis datos")])


def pages(blocks, limit=760):
    """Divide en espacios si un bloque es largo; no elimina texto ni fuentes."""
    output, page = [], ""
    for block in blocks:
        for word in (block + "\n\n").split(" "):
            while len(word) > limit:
                if page:
                    output.append(page.strip())
                    page = ""
                output.append(word[:limit])
                word = word[limit:]
            if len(page) + len(word) + 1 > limit:
                output.append(page.strip())
                page = ""
            page += word + " "
    if page.strip():
        output.append(page.strip())
    return output or ["Sin información adicional guardada."]


def detail(context, widget, money, missing_label, *, sources=False, page=0):
    d = context["diagnostic"]
    result = d.get("result") or {}
    blocks = []
    if sources:
        catalog = d["answers"]["estimate_catalog"]
        blocks.append(f"Versión de FinancIAl: {catalog['version']}. Preparada: {catalog.get('prepared_at')}. Revisión prevista: {catalog.get('review_by')}. "
                      "Las referencias respaldan conceptos o supuestos; no acreditan el cobro exacto de tu negocio.")
        for source in result.get("sources", []):
            if isinstance(source, dict):
                blocks.append("\n".join(f"{key}: {value}" for key, value in source.items()))
            else:
                blocks.append(str(source))
        blocks.extend(result.get("assumptions", []))
        blocks.append(ORIENTATION_NOTICE)
        blocks.append("Los montos propios conservan su origen declarado. Las fechas supuestas se usan para presupuestar, no como vencimientos oficiales. "
                      "Los pagos anuales se pagan en su fecha; no se dividen entre doce para simular caja.")
    else:
        economic = result.get("economic_assessment") or {}
        projection = result.get("projection") or {}
        if economic.get("capacity"):
            blocks.append(f"Por qué la capacidad orientativa es {economic['capacity']}")
            blocks.extend(capacity_breakdown(result))
        for cost in result.get("costs", []):
            if cost["applicability"] == "not_applicable":
                amount = "No incluido en este escenario"
            elif cost["min"] is None or cost["applicability"] == "to_confirm":
                amount = "Pendiente de revisar; no incluido en la suma"
            else:
                amount = f"{money(cost['min'])} a {money(cost['max'])} {FREQUENCIES.get(cost['frequency'], 'con frecuencia por confirmar')}"
            extra = " Ya está en los gastos mensuales; no se suma otra vez." if cost.get("included_in_baseline") else ""
            if cost.get("paid") is not None:
                extra += f" Ya pagado: {money(cost['paid'])}."
            if cost.get("due_date"):
                extra += f" Pago: {cost['due_date']}" + (" (fecha supuesta)." if cost.get("assumed_date") else " (fecha declarada).")
            blocks.append(f"• {cost['label']}: {amount}. Origen: {cost.get('origin') or 'datos del negocio'}.{extra}")
        projection = result.get("projection") or {}
        for month in projection.get("months", []):
            blocks.append(f"Caja de {month['month'][:7]}: cobros {money(month['inflows'])}, pagos {money(month['outflows'])}, saldo final {money(month['closing_balance'])}.")
        blocks.append(f"Mayor faltante de caja conocido: {money(projection.get('maximum_cash_gap'))}. "
                      "Se consideran retiros y deudas. Un saldo positivo no resuelve los costos pendientes.")
        missing = (result.get("economic_assessment") or {}).get("missing_fields", d.get("missing_fields", []))
        blocks.append("Para completar el presupuesto:\n" + ("\n".join(missing_label(key, context) for key in missing) or
                      "Verifica los costos y permisos con sus proveedores u organismos; la evaluación económica es orientativa."))
    content = pages(blocks)
    page = min(max(0, page), len(content) - 1)
    prefix = "gsources_" if sources else "gdetails_"
    options = []
    if page:
        options.append((prefix + str(page - 1), "Anterior"))
    if page + 1 < len(content):
        options.append((prefix + str(page + 1), "Siguiente"))
    options.append(("gsummary", "Volver al resumen"))
    return widget(context, f"{'Fuentes y supuestos' if sources else 'Costos y próximos pasos'} · {page + 1}/{len(content)}\n\n{content[page]}", options)
