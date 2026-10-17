"""Diagnóstico económico por WhatsApp, persistido y no bloqueante.

El catálogo es una lista de candidatos, NO una autorización del negocio.
Con los aranceles/reglas aún pendientes se entrega un presupuesto parcial.
No modifica movimientos financieros, perfil, roadmap ni tablas de catálogo.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from core.formalization_engine import (
    RULES_CONFIG, RULES_VERSION, catalog_applicability, convert_clp, evaluate_diagnostic, month_after,
)
from core.formalization_errors import (
    formalization_error_code, formalization_error_message, safe_sqlstate,
)
from db.formalization_diagnostics import (
    FormalizationConflictError, confirm_formalization_revision,
    get_formalization_context, save_formalization_draft,
)
from schemas.formalization_diagnostics import FormalizationItemInput


logger = logging.getLogger("financial")
ENTRY_ID = "menu_diagnostico"
PASS_TO_AI = "__DIAGNOSTIC_AI__"
_ENTRY = {"diagnostico de formalizacion", "diagnostico economico", "mi diagnostico",
          "retomar diagnostico", "rehacer diagnostico", "diagnostico"}
_EXPENSE_KEYS = ("arriendo", "insumos", "servicios", "transporte", "otros")
_EDIT_FIELDS = {"period": "Mes observado", "expenses": "Gastos actuales", "income": "Ingresos",
                "cash": "Efectivo disponible", "owner": "Retiro personal", "debt": "Cuotas de deudas",
                "representative": "Proyección mensual", "projection_date": "Inicio y preparación",
                "costs": "Costos y permisos", "refresh": "Actualizar registros"}
_SEQUENCE = ("location", "period", "expenses", "income", "cash", "owner", "debt",
             "representative", "projection_date", "preparation", "calendar", "costs", "review")


def normalize(value: str) -> str:
    return " ".join("".join(c for c in unicodedata.normalize("NFD", value.lower())
                            if unicodedata.category(c) != "Mn").split())


def is_diagnostic_entry(message: str) -> bool:
    return message.strip().lower() == ENTRY_ID or normalize(message) in _ENTRY


def _today():
    return datetime.now(ZoneInfo("America/Santiago")).date()


def _money(value):
    return "pendiente" if value is None else "$" + f"{value:,}".replace(",", ".")


def _amount(message: str) -> int:
    text = message.strip().lower().replace("pesos", "").replace("clp", "").replace("$", "").strip()
    if not re.fullmatch(r"(?:\d{1,3}(?:\.\d{3})+|\d+)", text):
        raise ValueError("Escribe pesos enteros, por ejemplo 15000 o $15.000; 0 si no corresponde.")
    amount = int(text.replace(".", ""))
    if amount > 9_000_000_000_000:
        raise ValueError("El importe es demasiado grande para este diagnóstico.")
    return amount


def _token(d, action):
    return f"diag:{d['id']}:{d['lock_version']}:{action}"


def _widget(context, body, options=None, *, listing=False):
    d = context["diagnostic"]
    result = {"type": "list" if listing else "buttons", "body": body,
              "options": [(_token(d, action), label) for action, label in (options or [("resume", "Continuar")])]}
    if listing:
        result["button_text"] = "Elegir opción"
    return result


def _save(user, context, patch=None, *, items=None, delete=None, refresh=False):
    d = context["diagnostic"]
    return save_formalization_draft(
        user["id"], d["id"], d["lock_version"], patch or {}, items=items,
        delete_item_keys=delete, refresh_sources=refresh,
        reopen=d["workflow_status"] == "confirmed",
    )


def _set_step(user, context, step, answers=None, **extra):
    patch = {"conversation_status": "active", "current_step": step, "pending_field": step, **extra}
    if answers is not None:
        patch["answers"] = answers
    return _save(user, context, patch)


def _catalog_items(context):
    return sorted((item for item in context["items"] if item.get("catalog_version_id")),
                  key=lambda item: item["item_key"])


def _input(item, **changes):
    data = {key: item[key] for key in FormalizationItemInput.model_fields if key in item}
    return {**data, **changes}


def _seed_catalog(user, context):
    """Se carga una vez por alcance; las siguientes respuestas usan el snapshot."""
    candidates = context.get("fresh_sources", {}).get("catalog", [])
    # Choose the most specific version per concept. Equal-specificity conflicting
    # tariffs remain unknown rather than arbitrarily selecting a price.
    grouped = {}
    for row in candidates:
        grouped.setdefault(row["code"], []).append(row)
    items = []
    for code, rows in grouped.items():
        score = lambda row: sum(row.get(key) is not None for key in ("activity_code", "comuna_code", "location_type"))
        best = max(map(score, rows))
        top = [row for row in rows if score(row) == best]
        row = sorted(top, key=lambda r: str(r["id"]))[0]
        facts = _profile_facts(context)
        applies = catalog_applicability(row, facts)
        items.append({"item_key": "catalog:" + code, "catalog_version_id": row["id"],
                      "label": row["title"], "item_kind": "future_operating" if row["frequency"] == "monthly" else "initial_payment",
                      "economic_nature": {"investment": "investment", "tax": "tax", "guarantee": "guarantee"}.get(row["cost_nature"], "expense"),
                      "cash_direction": "outflow", "source_type": "catalog", "frequency": row["frequency"],
                      "applicability": "not_applicable" if applies == "not_applicable" else "to_confirm",
                      "applicability_reason": "Condiciones del catálogo contrastadas con el perfil; lo desconocido queda pendiente",
                      "metadata": {"ambiguous_scope": len(top) > 1}})
    keys = {item["item_key"] for item in items}
    retired = [item["item_key"] for item in _catalog_items(context) if item["item_key"] not in keys]
    if not items and not retired:
        return context
    return _save(user, context, items=items, delete=retired)


def _profile_facts(context):
    profile = context["diagnostic"].get("profile_snapshot") or {}
    rubro = normalize(profile.get("rubro") or "").removeprefix("rubro_")
    facts = {"inicio_sii": profile.get("inicio_sii")}
    if rubro == "alimentos":
        facts["food_activity"] = True
    elif rubro == "textil":
        facts["food_activity"] = False
    return facts


def _monthly_item(key, label, amount, *, inflow=False, kind="current_operating", nature=None, **extra):
    return {"item_key": key, "label": label, "item_kind": kind,
            "economic_nature": nature or ("revenue" if inflow else "expense"),
            "cash_direction": "inflow" if inflow else "outflow", "applicability": "applies",
            "amount_clp_min": amount, "amount_clp_max": amount, "amount_state": "confirmed",
            "unit": "CLP", "source_type": "user", "frequency": "monthly", **extra}


def _budget_items(context):
    a, d = context["diagnostic"].get("answers", {}), context["diagnostic"]
    items = []
    if a.get("expenses_confirmed"):
        items.append(_monthly_item("current_expenses", "Gastos actuales del negocio", a["expenses_total"],
                                   metadata={"breakdown": a.get("expenses_breakdown"), "scope": "solo operación recurrente"}))
    if a.get("income_confirmed"):
        items.append(_monthly_item("current_income", "Ventas y servicios del periodo", a["income_total"], inflow=True))
    if "projected_income" in a:
        items.append(_monthly_item("projected_income", "Ingresos mensuales proyectados", a["projected_income"], inflow=True,
                                   kind="future_operating", replaces_item_key="current_income"))
    if "projected_expenses" in a:
        items.append(_monthly_item("projected_expenses", "Gastos operativos mensuales proyectados", a["projected_expenses"],
                                   kind="future_operating", replaces_item_key="current_expenses"))
    if a.get("owner_confirmed"):
        items.append(_monthly_item("owner", "Retiro personal necesario", a["owner_amount"],
                                   kind="owner_withdrawal", nature="owner_withdrawal"))
    if a.get("debt_confirmed"):
        items.append(_monthly_item("debt", "Cuotas de deuda (sin duplicar intereses)", a["debt_amount"],
                                   kind="other_cashflow", nature="loan_principal"))
    if "preparation_amount" in a:
        items.append({**_monthly_item("preparation", "Otros pagos de espera/preparación", a["preparation_amount"], kind="initial_payment"),
                      "frequency": "once", "amount_paid_clp": 0, "due_date": _today().isoformat()})
    return items


def _save_budgets(user, context):
    items = _budget_items(context)
    expected = {item["item_key"] for item in items}
    obsolete = [item["item_key"] for item in context["items"] if not item.get("catalog_version_id")
                and item["item_key"] not in expected]
    removed_ids = {item["id"] for item in context["items"] if item["item_key"] in obsolete}
    for item in _catalog_items(context):
        if item.get("replaces_item_id") in removed_ids:
            # A changed observed period invalidates the replacement link, not
            # the evidence/quote. Ask to review that applicability again.
            items.append(_input(item, applicability="to_confirm", replaces_item_key=None))
    return _save(user, context, items=items, delete=obsolete)


def _prompt(context):
    d = context["diagnostic"]
    a, step = d.get("answers", {}), d.get("current_step", "location")
    financial = d.get("financial_snapshot") or {}
    if step == "location":
        profile = d.get("profile_snapshot") or {}
        return _widget(context, f"📊 Diagnóstico de formalización\n{profile.get('rubro_raw') or profile.get('rubro')} · {profile.get('comuna')}\n\n"
                       "Reutilizaré tu perfil y movimientos; no cambiaré tus registros. ¿Dónde funcionará el negocio?\n"
                       "Puedes salir con el menú o hacer otra pregunta; el borrador se conserva.",
                       [("vivienda", "En mi vivienda"), ("local", "En un local"), ("otra", "Otra modalidad")])
    if step == "period":
        return _widget(context, f"Usaremos {str(d['period_start'])[:7]} como mes observado y tres meses futuros.\n"
                       "¿Usamos ese mes? Para otro mes completo escribe AAAA-MM (ejemplo: 2026-09).",
                       [("yes", "Usar ese mes"), ("pause", "Pausar")])
    if step == "expenses":
        categories = financial.get("categories", [])
        detail = "; ".join(f"{row['category']}: {_money(row['total'])}" for row in categories if row["movement_type"] == "expense")
        return _widget(context, f"Gastos registrados del mes: {_money(financial.get('expense_total'))}.\n{detail[:350]}\n\n"
                       "Confirma si son TODOS tus gastos operativos mensuales, sin inversiones, cuotas ni retiros. "
                       "Si faltan o hay compras únicas, escribe los TOTALES del mes:\n"
                       "arriendo=0; insumos=0; servicios=0; transporte=0; otros=0\n"
                       "Incluye en otros cualquier gasto adicional del negocio; no se suman otra vez a los registros.",
                       [("yes", "Son todos operativos"), ("pause", "Pausar")])
    if step == "income":
        base = financial.get("operating_income_total", 0)
        return _widget(context, f"Ventas/servicios registrados: {_money(base)}.\n"
                       f"Aportes excluidos de ventas: {_money(financial.get('capital_contributions_total', 0))}.\n"
                       f"Otros ingresos por revisar: {_money(financial.get('other_income_total', 0))}.\n\n"
                       "¿Están completas las ventas/servicios del mismo mes? Confirma o escribe SOLO lo faltante. "
                       "Para corregir todo: total 500000. No incluyas préstamos, aportes ni transferencias.",
                       [("yes", "Ventas completas"), ("pause", "Pausar")])
    questions = {
        "cash": "¿Cuánto efectivo tienes HOY disponible para el negocio, después de abonos ya pagados? No incluyas créditos o aportes hipotéticos.",
        "owner": "¿Cuánto necesitas retirar cada mes para uso personal? Escribe 0 si evaluarás sin retiros; no incluirá sostenibilidad del hogar.",
        "debt": "¿Cuánto pagarás mensualmente en cuotas de deudas, fuera de los gastos operativos anteriores? Incluye cada cuota una sola vez. 0 si no tienes.",
        "representative": "¿Los ingresos y gastos confirmados representan cada uno de los próximos tres meses? Si NO, escribe: ingresos=500000; gastos=300000. No uses un mes excepcional sin ajustar.",
        "projection_date": "¿En qué mes comenzarás el escenario posterior a formalizarte? Escribe AAAA-MM (un mes futuro). Incluiremos por separado los pagos durante la espera.",
        "preparation": "¿Qué otros pagos tendrás desde hoy hasta comenzar (espera, garantías, operación sin ventas autorizadas)? Escribe el total pendiente, sin repetir permisos/equipos que revisaremos después. 0 si no hay; 'no sé' si no puedes acotarlo.",
        "calendar": "¿Aceptas este calendario prudente para el presupuesto mensual: pagos al inicio y cobros al cierre de cada mes? Las fechas explícitas de permisos/cotizaciones se respetan. Si no representa tu caso, quedará pendiente validar fechas antes de clasificar.",
    }
    if step in questions:
        options = [("yes", "Sí, representa"), ("unknown", "No sé aún"), ("pause", "Pausar")] if step in {"calendar", "representative"} else [("pause", "Pausar")]
        return _widget(context, questions[step], options)
    if step == "costs":
        page = a.get("cost_page", 0)
        rows = _catalog_items(context)[page * 7:(page + 1) * 7]
        options = [("cost_" + item["item_key"].split(":", 1)[1], item["label"][:24]) for item in rows]
        if page:
            options.append(("previous_costs", "Costos anteriores"))
        if len(_catalog_items(context)) > (page + 1) * 7:
            options.append(("next_costs", "Más costos"))
        options.append(("costs_done", "Terminar revisión"))
        return _widget(context, "Revisa costos candidatos. No todos aplican a tu negocio. "
                       "Cada uno conserva su fuente y sus pendientes; no inventaremos aranceles.\n"
                       "Los costos no revisados quedan pendientes. Puedes terminar con un diagnóstico parcial.", options, listing=True)
    if step in {"cost_applicability", "cost_amount"}:
        item = next(i for i in context["items"] if i["item_key"] == a["selected_cost"])
        cat = item["catalog_snapshot"]
        if step == "cost_applicability":
            return _widget(context, f"{item['label']}\n{cat.get('description', '')[:330]}\n"
                           f"Condición: {cat.get('mandatory_status')}; revisión de fuente: {cat.get('review_status')}.\n\n"
                           "¿Este costo aplica a tu caso? Si no has verificado el permiso/obligación, elige 'No sé'. "
                           "Esto no sustituye una validación municipal o contable.",
                           [("yes", "Sí, aplica"), ("no", "No aplica"), ("unknown", "No sé")])
        return _widget(context, f"{item['label']}: ingresa un presupuesto respaldado en CLP. Ejemplo:\n"
                       "monto=50000; pagado=0; fecha=2026-11-15; frecuencia=unico; incluido=no; respaldo=cotización proveedor\n\n"
                       "Frecuencia: unico, mensual o anual. Fecha: vencimiento real; en mensual puede omitirse. "
                       "Incluido=si SOLO si ya está en los gastos operativos. En cambios operativos indica el TOTAL futuro, no un incremento. "
                       "Puedes escribir 'no sé'. Un arancel pendiente no se valida solo por indicar un monto.",
                       [("unknown", "No sé el monto"), ("pause", "Pausar")])
    if step == "edit":
        return _widget(context, "¿Qué dato quieres cambiar? Conservaremos el resto y las versiones anteriores.",
                       [("edit_" + key, label) for key, label in _EDIT_FIELDS.items()], listing=True)
    if step == "review":
        return _widget(context, f"Revisa tu presupuesto ({str(d['period_start'])[:7]}):\n"
                       f"Ingresos: {_money(a.get('income_total'))}\nGastos operativos: {_money(a.get('expenses_total'))}\n"
                       f"Efectivo: {_money(a.get('available_cash'))}\nRetiros/mes: {_money(a.get('owner_amount'))}\n"
                       f"Deudas/mes: {_money(a.get('debt_amount'))}\n"
                       "Se mostrará por separado el costo inicial y los pagos futuros. "
                       "Las obligaciones sin validar impedirán asignar alta/media/baja. ¿Guardamos esta versión?",
                       [("confirm", "Guardar diagnóstico"), ("edit", "Cambiar datos"), ("pause", "Pausar")])
    return _result_widget(context)


def _result_widget(context):
    d = context["diagnostic"]
    result = d.get("result") or {}
    label = d.get("capacity") or ("No evaluable" if d.get("assessment_status") == "not_evaluable" else "Parcial: faltan datos/validaciones")
    return _widget(context, f"📊 Diagnóstico: {label}\n"
                   f"Resultado operativo actual: {_money(result.get('current_operating_result'))}/mes\n"
                   f"Resultado futuro conocido: {_money(result.get('future_known_operating_result'))}/mes\n"
                   f"Pagos iniciales pendientes conocidos: {_money(result.get('initial_remaining_max'))}\n"
                   f"Pendientes: {len(d.get('missing_fields') or [])}. Horizonte: tres meses.\n\n"
                   "Lo conocido excluye importes pendientes, NO significa costo total. "
                   "Reglas propuestas de FinancIAl; no es autorización ni evaluación crediticia. "
                   "Escribe 'menú' para volver.",
                   [("details", "Costos y balance"), ("sources", "Ver fuentes"), ("edit", "Cambiar datos")])


def _details(context, *, sources=False):
    d = context["diagnostic"]
    result = d.get("result") or {}
    if sources:
        rows = result.get("sources", [])
        return {"type": "text", "body": "Fuentes del catálogo (la revisión no equivale a vigencia legal):\n\n" + "\n\n".join(
            f"{s.get('organism') or s.get('kind', 'Fuente')} | {s.get('url') or s.get('document', '')}\n"
            f"Revisión: {s.get('reviewed_at', 'pendiente')} | {s.get('scope', '')}" for s in rows)
            + "\n\nPara continuar: mi diagnóstico. Para cambiar: rehacer diagnóstico."}
    rows = [f"• {c['label']}: {_money(c['max'])} | {c['frequency']} | {c['applicability']} | {c['mandatory_status']}"
            + (" | ya incluido (no se suma)" if c['included_in_baseline'] else "") for c in result.get("costs", [])]
    projection = result.get("projection") or {}
    months = [f"{m['month'][:7]}: cobros {_money(m['inflows'])}, pagos {_money(m['outflows'])}, "
              f"caja cierre {_money(m['closing_balance'])}" for m in projection.get("months", [])]
    reasons = {"INITIAL_CASH_GAP": "Revisar los pagos de arranque: falta efectivo inicial.",
               "BASE_PAYMENT_UNCOVERED": "Ajustar el calendario: hay un pago anterior al dinero que lo cubriría.",
               "BASE_CASH_BURN": "Revisar gastos recurrentes: los pagos superan los cobros proyectados.",
               "RESERVE_NOT_COVERED": "La caja no alcanza la reserva objetivo de un mes de pagos recurrentes.",
               "STRESS_CASH_BURN": "Con ventas 15% menores, la caja disminuye durante el horizonte.",
               "STRESS_PAYMENT_UNCOVERED": "Con ventas 15% menores hay pagos sin cobertura."}
    explanation = [reasons[key] for key in projection.get("reasons", []) if key in reasons]
    return {"type": "text", "body": "Costos iniciales y futuros (CLP)\n" + "\n".join(rows)
            + "\n\nCaja de la parte conocida, no clasificación si hay pendientes:\n" + "\n".join(months)
            + f"\nSaldo mínimo: {_money(projection.get('minimum_balance'))}\nReserva objetivo: {_money(projection.get('reserve_target'))}"
            + "\n\nFactores de caja conocidos (no calificación si faltan costos):\n" + "\n".join(explanation)
            + "\n\nFalta confirmar:\n" + "\n".join(_missing_label(key, context) for key in d.get("missing_fields", []))
            + "\n\nSupuestos:\n" + "\n".join(result.get("assumptions", []))
            + "\n\nPara continuar: mi diagnóstico. Para cambiar: rehacer diagnóstico."}


def _missing_label(key, context):
    titles = {"expenses_confirmed": "Cobertura de los gastos del mes", "income_confirmed": "Cobertura de ventas/servicios",
              "representative": "Representatividad de la proyección", "calendar_confirmed": "Calendario de cobros y pagos",
              "preparation_confirmed": "Costos durante la preparación y espera", "owner_confirmed": "Retiros personales",
              "debt_confirmed": "Cuotas de deudas", "available_cash": "Efectivo disponible",
              "catalog_coverage_confirmed": "Validar cobertura del catálogo y permisos/costos específicos del negocio",
              "financial_sources": "Recuperar movimientos del periodo"}
    prefixes = {"amount": "Presupuesto pendiente", "applicability": "Confirmar si aplica",
                "source_review": "Validación de tarifa/regla pendiente", "paid": "Confirmar abonos",
                "schedule": "Fecha/calendario pendiente", "evidence": "Respaldar la estimación",
                "replacement": "Revisar gasto sustituido", "schedule_total": "Revisar suma de cuotas",
                "catalog_scope": "Resolver tarifas contradictorias del catálogo",
                "financing_commitment": "Confirmar compromiso del aporte/préstamo"}
    for prefix, label in prefixes.items():
        if key.startswith(prefix + ":"):
            item_key = key[len(prefix) + 1:]
            title = next((item["label"] for item in context["items"] if item["item_key"] == item_key), item_key)
            return f"• {label}: {title}"
    return "• " + titles.get(key, key.replace("_", " "))


def _new(user):
    today = _today()
    end = today.replace(day=1)
    scope = lambda text: re.sub(r"[^a-z0-9]+", "_", normalize(text or "")).strip("_") or "no_definido"
    return save_formalization_draft(user["id"], str(uuid4()), 0, {
        "current_step": "location", "pending_field": "location", "conversation_status": "active",
        "period_start": month_after(end, -1), "period_end": end,
        "projection_start": month_after(end), "activity_code": scope(user.get("rubro")),
        "comuna_code": scope(user.get("comuna")), "rules_version": RULES_VERSION,
        "rules_config": dict(RULES_CONFIG),
    })


def _next(user, context, answers, *, items=None):
    d = context["diagnostic"]
    returning = answers.pop("return_to_review", False)
    step = "review" if returning else _SEQUENCE[_SEQUENCE.index(d["current_step"]) + 1]
    if returning and d["current_step"] in {"income", "expenses"}:
        step = "representative"
        answers["return_to_review"] = True
    context = _save(user, context, {"current_step": step, "pending_field": step, "answers": answers,
                                  "conversation_status": "active"}, items=items)
    return _prompt(context)


def _cost_amount(item, text, context):
    fields = {normalize(key): value.strip() for key, value in
              (part.strip().split("=", 1) for part in text.split(";") if "=" in part)}
    if not {"monto", "pagado", "frecuencia", "incluido", "respaldo"} <= fields.keys():
        raise ValueError("Faltan monto, pagado, frecuencia, incluido o respaldo. Usa el formato del ejemplo.")
    amount, paid = _amount(fields["monto"]), _amount(fields["pagado"])
    frequency = {"unico": "once", "mensual": "monthly", "anual": "annual"}.get(normalize(fields["frecuencia"]))
    included = normalize(fields["incluido"])
    if not frequency or included not in {"si", "no"} or len(fields["respaldo"].strip()) < 5:
        raise ValueError("Indica frecuencia válida, incluido=si/no y un respaldo concreto.")
    if frequency == "monthly" and paid:
        raise ValueError("Los abonos se registran sobre pagos únicos/anuales, no contra todos los meses futuros.")
    due = date.fromisoformat(fields["fecha"].strip()) if fields.get("fecha") else None
    if frequency != "monthly" and due is None:
        raise ValueError("Indica la fecha real del vencimiento.")
    if due and due < _today() and amount > paid:
        raise ValueError("Para un pago pendiente vencido, indica cuándo lo pagarás efectivamente desde hoy.")
    if included == "si" and frequency != "monthly":
        raise ValueError("Solo un pago operativo mensual puede estar incluido en el presupuesto mensual.")
    kind = "initial_payment" if frequency == "once" or (due and due < date.fromisoformat(context["diagnostic"]["projection_start"])) else "future_operating"
    changes = {"applicability": "applies", "amount_clp_min": amount, "amount_clp_max": amount,
               "amount_paid_clp": paid, "amount_state": "quoted", "unit": "CLP", "frequency": frequency,
               "due_date": due, "item_kind": kind, "included_in_baseline": included == "si",
               "evidence": {"description": fields["respaldo"].strip(), "declared_on": _today().isoformat()}}
    if item["item_key"] == "catalog:C24":
        if frequency != "monthly" or included != "no":
            raise ValueError("Para cambios operativos usa el total futuro mensual, incluido=no: reemplazará los gastos actuales.")
        if "projected_expenses" in context["diagnostic"]["answers"]:
            raise ValueError("Ya ajustaste los gastos en Proyección mensual. Edita esa proyección y deja C24 no aplicable para evitar duplicarlos.")
        changes["replaces_item_key"] = "current_expenses"
    return _input(item, **changes)


def handle_diagnostic_message(user, message, *, external_action=False, message_id=None):
    """Retorna None para otros módulos; PASS_TO_AI evita triggers por substring.

    Los botones llevan diagnóstico y lock_version. Una respuesta de una pregunta
    antigua no modifica la actual. Las lecturas fallidas no significan caja cero.
    """
    entry, raw = is_diagnostic_entry(message), message.strip()
    diagnostic_button = raw.startswith("diag:")
    stage = "load"
    try:
        # Existing route tests and legacy callers sometimes supply non-UUID IDs.
        UUID(str(user.get("id")))
        context = get_formalization_context(user["id"])
        d = context.get("diagnostic")
        stage = "flow"
        action = normalize(raw)
        if diagnostic_button:
            parts = raw.split(":", 3)
            if len(parts) != 4 or not d or parts[1:3] != [d["id"], str(d["lock_version"])]:
                return "Ese botón corresponde a otra pregunta. Escribe 'mi diagnóstico' para recuperar el paso actual."
            action = parts[3]
        if entry:
            if not d or d["workflow_status"] in {"cancelled", "archived"}:
                stage = "create"
                context = _new(user)
            elif d["workflow_status"] == "confirmed":
                if normalize(raw) != "rehacer diagnostico":
                    return _result_widget(context)
                context = _set_step(user, context, "edit")
            else:
                stage = "resume"
                context = _save(user, context, {"conversation_status": "active"})
            return _prompt(context)
        if not d:
            return None
        if external_action:
            reset = normalize(raw) in {"reset", "reiniciar", "menu_reiniciar", "empezar de nuevo"}
            if reset and d["workflow_status"] in {"draft", "ready", "confirmed"}:
                _save(user, context, {"workflow_status": "archived" if d["workflow_status"] == "confirmed" else "cancelled",
                                      "conversation_status": "closed"})
            elif d["workflow_status"] in {"draft", "ready"} and d["conversation_status"] == "active":
                patch = {"conversation_status": "paused"}
                _save(user, context, patch)
            return None
        if action in {"sources", "details"} and diagnostic_button and d.get("result"):
            return _details(context, sources=action == "sources")
        if action == "edit" and diagnostic_button:
            return _prompt(_set_step(user, context, "edit"))
        if d["workflow_status"] not in {"draft", "ready"} or d["conversation_status"] != "active":
            return None
        if action == "pause" or normalize(raw) in {"pausar diagnostico", "cancelar diagnostico"}:
            _save(user, context, {"conversation_status": "paused"})
            return "Conservé tu borrador. Para continuar escribe 'retomar diagnóstico'."
        if action == "resume" and diagnostic_button:
            return _prompt(context)
        a, step = dict(d.get("answers") or {}), d["current_step"]
        action = {"si": "yes", "no se": "unknown"}.get(action, action)
        # Capture only expected structured answers. Everything else exits safely.
        valid = diagnostic_button or action in {"yes", "no", "unknown"} or bool(re.fullmatch(r"[\d.,$+\-\s]+(?:pesos|clp)?", raw.lower()))
        valid = valid or "=" in raw or bool(re.fullmatch(r"\d{4}-\d{2}", raw)) or bool(re.fullmatch(r"total\s+[\d.$\s]+", raw.lower()))
        if not valid:
            _save(user, context, {"conversation_status": "paused"})
            return PASS_TO_AI
        if step == "edit" and action.startswith("edit_") and action[5:] in _EDIT_FIELDS:
            target = action[5:]
            if target == "refresh":
                for key in ("expenses_confirmed", "income_confirmed", "representative", "expenses_total", "income_total",
                            "expenses_breakdown", "projected_income", "projected_expenses", "return_to_review"):
                    a.pop(key, None)
                a["cost_page"] = 0
                scope = lambda text: re.sub(r"[^a-z0-9]+", "_", normalize(text or "")).strip("_") or "no_definido"
                previous = d.get("catalog_fingerprint")
                context = _save(user, context, {"answers": a, "current_step": "expenses", "pending_field": "expenses",
                                               "conversation_status": "active", "activity_code": scope(user.get("rubro")),
                                               "comuna_code": scope(user.get("comuna"))}, refresh=True)
                if context["diagnostic"].get("catalog_fingerprint") != previous:
                    context = _seed_catalog(user, context)
                return _prompt(_save_budgets(user, context))
            # Period changes invalidate the observed amounts, not private quotes.
            if target not in {"period", "costs", "projection_date"}:
                a["return_to_review"] = True
            return _prompt(_set_step(user, context, target, a))
        if step == "location" and action in {"vivienda", "local", "otra"}:
            context = _set_step(user, context, "period", a, location_type=action)
            context = _seed_catalog(user, context)
            return _prompt(context)
        if step == "period":
            if action == "yes":
                return _next(user, context, a)
            start = date.fromisoformat(raw + "-01")
            if start >= _today().replace(day=1):
                raise ValueError("Selecciona un mes ya terminado para comparar datos completos.")
            for key in ("expenses_confirmed", "income_confirmed", "representative", "expenses_total", "income_total",
                        "expenses_breakdown", "projected_income", "projected_expenses", "return_to_review"):
                a.pop(key, None)
            context = _set_step(user, context, "expenses", a, period_start=start, period_end=month_after(start))
            return _prompt(_save_budgets(user, context))
        if step == "expenses":
            financial = d["financial_snapshot"]
            if action == "yes":
                a["expenses_total"] = int(financial["expense_total"])
                a["expenses_breakdown"] = financial["categories"]
            else:
                fields = dict(part.strip().split("=", 1) for part in normalize(raw).split(";") if "=" in part)
                if set(fields) != set(_EXPENSE_KEYS):
                    raise ValueError("Indica los cinco totales: arriendo, insumos, servicios, transporte y otros (0 si no aplica).")
                a["expenses_breakdown"] = {key: _amount(value) for key, value in fields.items()}
                a["expenses_total"] = sum(a["expenses_breakdown"].values())
            a["expenses_confirmed"] = True
            # A changed baseline requires a new representativity confirmation.
            a.pop("representative", None)
            return _next(user, context, a)
        if step == "income":
            base = int(d["financial_snapshot"]["operating_income_total"])
            a["income_total"] = base if action == "yes" else (_amount(raw[6:]) if raw.lower().startswith("total ") else base + _amount(raw))
            a["income_confirmed"] = True
            a.pop("representative", None)
            return _next(user, context, a)
        if step in {"cash", "owner", "debt", "preparation"}:
            key = {"cash": "available_cash", "owner": "owner_amount", "debt": "debt_amount", "preparation": "preparation_amount"}[step]
            a[key] = None if action == "unknown" else _amount(raw)
            if a[key] is None:
                a.pop(key)
            if step != "cash":
                a[{"owner": "owner_confirmed", "debt": "debt_confirmed", "preparation": "preparation_confirmed"}[step]] = action != "unknown"
            else:
                a["cash_as_of"] = _today().isoformat()
            return _next(user, context, a)
        if step == "representative":
            if action in {"yes", "unknown"}:
                a["representative"] = action == "yes"
                a.pop("projected_income", None)
                a.pop("projected_expenses", None)
            else:
                fields = dict(part.strip().split("=", 1) for part in normalize(raw).split(";") if "=" in part)
                if set(fields) != {"ingresos", "gastos"}:
                    raise ValueError("Indica ingresos=...; gastos=... para cada mes proyectado, o usa los botones.")
                a.update(projected_income=_amount(fields["ingresos"]), projected_expenses=_amount(fields["gastos"]), representative=True)
            return _next(user, context, a)
        if step == "projection_date":
            start = date.fromisoformat(raw + "-01")
            if start <= _today():
                raise ValueError("Selecciona el primer día de un mes futuro.")
            return _prompt(_set_step(user, context, "preparation", a, projection_start=start))
        if step == "calendar" and action in {"yes", "no", "unknown"}:
            a["calendar_confirmed"] = action == "yes"
            context = _set_step(user, context, "costs", a)
            return _prompt(_save_budgets(user, context))
        if step == "costs":
            if action == "previous_costs":
                a["cost_page"] = max(0, a.get("cost_page", 0) - 1)
                return _prompt(_set_step(user, context, "costs", a))
            if action == "next_costs":
                a["cost_page"] = a.get("cost_page", 0) + 1
                return _prompt(_set_step(user, context, "costs", a))
            if action == "costs_done":
                # The initial catalog is intentionally incomplete. This cannot
                # be set true by a user's yes/no or by filling numeric quotes.
                a["catalog_coverage_confirmed"] = False
                return _prompt(_set_step(user, context, "review", a))
            if action.startswith("cost_") and any(i["item_key"] == "catalog:" + action[5:] for i in _catalog_items(context)):
                a["selected_cost"] = "catalog:" + action[5:]
                return _prompt(_set_step(user, context, "cost_applicability", a))
        if step == "cost_applicability" and action in {"yes", "no", "unknown"}:
            item = next(i for i in context["items"] if i["item_key"] == a["selected_cost"])
            if action == "yes":
                cat = item["catalog_snapshot"]
                if catalog_applicability(cat, _profile_facts(context)) == "not_applicable":
                    raise ValueError("Las condiciones de este concepto contradicen tu perfil. Revisa los datos del perfil antes de incluirlo.")
                if (cat["pricing_method"] == "fixed" and cat["review_status"] == "reviewed"
                        and cat.get("unit") == "CLP" and not item["metadata"].get("ambiguous_scope")):
                    amount = convert_clp(Decimal(str(cat["amount_max"])), Decimal(1))
                    items = [_input(item, applicability="applies", amount_clp_min=amount, amount_clp_max=amount,
                                    amount_state="confirmed", amount_paid_clp=0, unit="CLP", due_date=_today())]
                else:
                    return _prompt(_set_step(user, context, "cost_amount", a))
            else:
                items = [_input(item, applicability="not_applicable" if action == "no" else "to_confirm",
                                applicability_reason="Declaración del usuario; no acredita exención ni autorización")]
            return _prompt(_save(user, context, {"current_step": "costs", "pending_field": "costs", "answers": a}, items=items))
        if step == "cost_amount":
            item = next(i for i in context["items"] if i["item_key"] == a["selected_cost"])
            values = (_input(item, applicability="applies", amount_clp_min=None, amount_clp_max=None, amount_state="pending")
                      if action == "unknown" else _cost_amount(item, raw, context))
            return _prompt(_save(user, context, {"current_step": "costs", "pending_field": "costs", "answers": a}, items=[values]))
        if step == "review" and action == "confirm":
            context = _save_budgets(user, context)
            stage = "calculate"
            assessment = evaluate_diagnostic(context)
            d = context["diagnostic"]
            request_id = uuid5(UUID(d["id"]), "confirm:" + (message_id or str(d["lock_version"])))
            stage = "confirm"
            saved = confirm_formalization_revision(user["id"], d["id"], d["lock_version"], str(request_id), assessment)
            if saved.get("replayed"):
                return _result_widget(get_formalization_context(user["id"], d["id"]))
            return _result_widget({**context, "diagnostic": saved["diagnostic"]})
        return _prompt(context)
    except FormalizationConflictError as error:
        return ("El diagnóstico o sus fuentes cambiaron. Escribe 'mi diagnóstico', elige Cambiar datos y "
                "Actualizar registros para revisar nuevamente el mes, sin perder las versiones anteriores.\n\n"
                "Código: " + formalization_error_code(error))
    except ValueError as error:
        if not entry and not diagnostic_button and not locals().get("d"):
            return None
        public_message = formalization_error_message(error, stage)
        if locals().get("context", {}).get("diagnostic"):
            try:
                prompt = _prompt(context)
            except Exception:
                # A malformed draft must not make the error handler fail too.
                return public_message
            # No raw exception/Pydantic payload is sent to WhatsApp. The prompt
            # only repeats the current question through its existing renderer.
            return {"type": "text", "body": public_message + "\n\n" + prompt["body"]}
        return public_message
    except Exception as error:
        # Do not log str(error) or exc_info: a driver error may include SQL,
        # failing rows, connection strings or the user's financial information.
        logger.error("No se pudo procesar el diagnóstico económico [%s] etapa=%s tipo=%s sqlstate=%s",
                     formalization_error_code(error), stage, type(error).__name__, safe_sqlstate(error))
        if entry or diagnostic_button or (locals().get("d") or {}).get("conversation_status") == "active":
            return formalization_error_message(error, stage)
        return None
