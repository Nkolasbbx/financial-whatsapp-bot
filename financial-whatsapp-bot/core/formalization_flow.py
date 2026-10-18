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
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from core import formalization_cost_guide as cost_guide
from core import formalization_presentation as presentation

from core.formalization_engine import (
    RULES_CONFIG, RULES_VERSION, evaluate_diagnostic, month_after,
)
from core.formalization_errors import (
    formalization_error_code, formalization_error_message, safe_sqlstate,
)
from core.formalization_financial_review import (
    EXPENSE_LABELS, INCOME_LABELS, clear_period_review, invalidate_projection,
    review_expenses, snapshot_amount,
)
from db.formalization_diagnostics import (
    FormalizationConflictError, confirm_formalization_revision,
    get_formalization_context, get_formalization_estimate_catalog, save_formalization_draft,
)
from schemas.formalization_diagnostics import FormalizationItemInput


logger = logging.getLogger("financial")
ENTRY_ID = "menu_diagnostico"
PASS_TO_AI = "__DIAGNOSTIC_AI__"
# Explicit release default, not an arbitrary inactive/draft catalog.
DEFAULT_ESTIMATE_VERSION = "financial-estimates-2026-10-17-v1"


class EstimateCatalogUnavailable(ValueError):
    """The guided flow must never fall back to the retired technical wizard."""


_ENTRY = {"diagnostico de formalizacion", "diagnostico economico", "mi diagnostico",
          "retomar diagnostico", "rehacer diagnostico", "diagnostico", "nuevo diagnostico"}
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


def _amount_fields(message: str, expected_keys) -> dict[str, int]:
    """Lee montos sin exigir espacios exactos ni sobrescribir campos repetidos."""
    fields = {}
    expected = set(expected_keys)
    for part in message.split(";"):
        if not part.strip():
            continue
        key, separator, value = part.partition("=")
        key = normalize(key)
        if not separator or key not in expected or key in fields:
            raise ValueError("Indica una sola vez cada campo del ejemplo, separado por punto y coma.")
        fields[key] = _amount(value)
    if set(fields) != expected:
        raise ValueError("Completa todos los campos del ejemplo; escribe 0 cuando no corresponda.")
    return fields


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
        items.append(_monthly_item("current_expenses", "Gastos del mes observado", a["expenses_total"],
                                   metadata={"breakdown": a.get("expenses_breakdown"),
                                             "oneoff_expenses": a.get("expense_oneoff"),
                                             "scope": "gastos del mes observado; incluye compras excepcionales declaradas"}))
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
                and not item["item_key"].startswith("estimate:") and item["item_key"] not in expected]
    removed_ids = {item["id"] for item in context["items"] if item["item_key"] in obsolete}
    for item in _catalog_items(context):
        if item.get("replaces_item_id") in removed_ids:
            # A changed observed period invalidates the replacement link, not
            # the evidence/quote. Ask to review that applicability again.
            items.append(_input(item, applicability="to_confirm", replaces_item_key=None))
    return _save(user, context, items=items, delete=obsolete)


def _legacy_notice(context):
    return _widget(context,
        "Ahora el diagnóstico se completa con preguntas simples, una a una. "
        "Tu evaluación anterior se conservará con sus datos y presupuestos; no se borrará. "
        "Para usar el nuevo asistente debes iniciar otra evaluación y revisar nuevamente sus respuestas. "
        "Tu perfil y movimientos financieros se reutilizan.",
        [("guided_restart", "Iniciar nuevo flujo")])


def _prompt(context):
    d = context["diagnostic"]
    if not cost_guide.is_guided(context):
        return _legacy_notice(context)
    a, step = d.get("answers", {}), d.get("current_step", "location")
    financial = d.get("financial_snapshot") or {}
    if cost_guide.is_guided(context):
        guided_prompt = cost_guide.prompt(context, _widget, _money)
        if guided_prompt:
            return guided_prompt
    if step == "location":
        profile = d.get("profile_snapshot") or {}
        disclosure = (a.get("estimate_catalog", {}).get("document", {}).get("disclosure", {}).get("text", "") + "\n\n") if cost_guide.is_guided(context) else ""
        rubro = (profile.get('rubro_raw') or profile.get('rubro') or '').removeprefix('rubro_').replace('_', ' ').capitalize()
        return _widget(context, f"📊 Diagnóstico de formalización\n{rubro} · {profile.get('comuna')}\n\n" + disclosure +
                       "Reutilizaré tu perfil y movimientos; no cambiaré tus registros. ¿Dónde funcionará el negocio?\n"
                       "Puedes salir con el menú o hacer otra pregunta; el borrador se conserva.",
                       [("vivienda", "En mi vivienda"), ("local", "En un local"), ("otra", "Otra modalidad")])
    if step == "period":
        return _widget(context, f"Usaremos {str(d['period_start'])[:7]} como mes observado y tres meses futuros.\n"
                       "¿Usamos ese mes? Para otro mes completo escribe AAAA-MM (ejemplo: 2026-09).",
                       [("yes", "Usar ese mes"), ("pause", "Pausar")])
    if step == "expenses":
        amounts = review_expenses(a, financial)
        detail = "\n".join(f"• {EXPENSE_LABELS[key]}: {_money(value)}" for key, value in amounts.items() if value)
        empty = "No tienes gastos registrados en este mes. El cero no significa que no hayas gastado.\n" if snapshot_amount(financial.get("expense_total")) == 0 else ""
        return _widget(context, f"💸 Gastos de {str(d['period_start'])[:7]}\n{empty}"
                       f"Total a revisar: {_money(sum(amounts.values()))}\n{detail}\n\n"
                       "¿Este es todo lo que gastaste en el negocio ese mes? No incluyas retiros personales ni cuotas de deuda. "
                       "Para reemplazar el total, escribe un número. Los ajustes solo se usan en este diagnóstico.",
                       [("yes", "Está completo"), ("expense_add", "Agregar lo que falta"), ("expense_categories", "Revisar categorías")])
    if step == "expense_add":
        return _widget(context, "¿Cuánto falta agregar a los gastos del mes? Escribe SOLO el monto que no está incluido, por ejemplo 15000. "
                       "Se sumará una vez al total del diagnóstico.")
    if step == "expense_categories":
        amounts = review_expenses(a, financial)
        keys = [key for key in EXPENSE_LABELS if key != "sin_desglose" or amounts.get(key)]
        return _widget(context, "Elige una categoría para revisar su total. Arriendo y servicios están agrupados en tus registros. "
                       "Puedes completar las categorías una a una; al terminar revisaremos el total.",
                       [("expense_cat_" + key, EXPENSE_LABELS[key]) for key in keys] + [("expense_done", "Volver al total")], listing=True)
    if step == "expense_category":
        key = a["expense_category"]
        return _widget(context, f"{EXPENSE_LABELS[key]}: {_money(review_expenses(a, financial).get(key, 0))}.\n"
                       "Escribe el TOTAL de esta categoría para el mes, por ejemplo 40000; 0 si no hubo gastos. "
                       "Reemplazará el monto anterior, no se sumará. No repitas gastos que dejaste en otra categoría.",
                       [("expense_keep", "Mantener monto"), ("pause", "Pausar")])
    if step == "expense_oneoff":
        return _widget(context, f"Gastos del mes confirmados: {_money(a['expenses_total'])}.\n"
                       "De ese total, ¿cuánto corresponde a compras o pagos excepcionales que NO repetirás cada mes, "
                       "como una máquina o un permiso por única vez? Escribe el monto o 0. "
                       "Se conservará en el mes observado, pero se descontará de la propuesta mensual futura.",
                       [("none", "Ninguno"), ("unknown", "No sé aún"), ("pause", "Pausar")])
    if step == "income":
        base = a.get("income_review", a.get("income_total", snapshot_amount(financial.get("operating_income_total"))))
        return _widget(context, f"💰 Ventas y servicios de {str(d['period_start'])[:7]}: {_money(base)}.\n"
                       f"Aportes excluidos de ventas: {_money(snapshot_amount(financial.get('capital_contributions_total', 0)))}.\n"
                       f"Otros ingresos por revisar: {_money(snapshot_amount(financial.get('other_income_total', 0)))}.\n\n"
                       "¿Están completas las ventas y servicios? Si hay ventas en «Otros ingresos», agrégalas solo si no están en este total. "
                       "No incluyas préstamos, aportes ni transferencias propias. Un número solo suma lo faltante; para reemplazar todo escribe: total 500000.",
                       [("yes", "Está completo"), ("income_add", "Agregar lo que falta"), ("income_details", "Revisar o corregir")])
    if step == "income_add":
        return _widget(context, "¿Cuánto falta agregar por ventas o servicios de ese mes? Escribe SOLO el monto que todavía no está incluido. "
                       "No sumes préstamos ni aportes personales.")
    if step == "income_details":
        detail = "\n".join(f"• {INCOME_LABELS.get(row['category'], 'Otros ingresos')}: {_money(snapshot_amount(row['total']))}"
                           for row in financial.get("categories", []) if row["movement_type"] == "income")
        return _widget(context, f"Desglose registrado del mes:\n{detail or 'No hay desglose de ingresos disponible.'}\n\n"
                       "Para corregir las ventas y servicios, escribe su TOTAL del mes. Reemplazará el total del diagnóstico. "
                       "Los movimientos originales se conservan.", [("income_done", "Volver al total"), ("pause", "Pausar")])
    if step in {"projection_income", "projection_expenses"}:
        question = "¿Cuánto esperas vender o cobrar por servicios cada mes?" if step == "projection_income" else "¿Cuánto gastarás normalmente cada mes en tu negocio? No incluyas las compras excepcionales, retiros personales ni cuotas."
        return _widget(context, question + " Escribe un número; 0 si no corresponde.")
    if step == "confirm_owner":
        return _widget(context, f"Anoté {_money(a['pending_owner'])} cada mes para tus gastos personales. "
                       "Ese retiro supera las ventas del mes que revisamos. ¿Es el monto que quieres considerar?",
                       [("yes", "Sí, es correcto"), ("no", "Corregir monto"), ("pause", "Pausar")])
    questions = {
        "cash": "¿Cuánto efectivo tienes HOY disponible para el negocio, después de abonos ya pagados? No incluyas créditos o aportes hipotéticos.",
        "owner": "¿Cuánto necesitas retirar cada mes para uso personal? Escribe 0 si evaluarás sin retiros; no incluirá sostenibilidad del hogar.",
        "debt": "¿Cuánto pagarás mensualmente en cuotas de deudas, fuera de los gastos operativos anteriores? Incluye cada cuota una sola vez. 0 si no tienes.",
        "representative": (f"Para cada uno de los próximos tres meses proponemos:\nVentas y servicios: {_money(a.get('income_total'))}\n"
                           f"Gastos habituales: {_money(a.get('expenses_total', 0) - a.get('expense_oneoff', 0)) if a.get('expense_oneoff_confirmed', True) else 'por confirmar'}\n"
                           "Los gastos excepcionales se conservan en el mes observado. ¿Estos montos representan un mes normal?"),
        "projection_date": f"¿Comenzamos la proyección en {str(d.get('projection_start', ''))[:7]}? Pulsa «Usar ese mes» o escribe otro mes futuro como AAAA-MM.",
        "preparation": "¿Qué otros pagos tendrás desde hoy hasta comenzar (espera, garantías, operación sin ventas autorizadas)? Escribe el total pendiente, sin repetir permisos/equipos que revisaremos después. 0 si no hay; 'no sé' si no puedes acotarlo.",
        "calendar": "¿Aceptas este calendario prudente para el presupuesto mensual: pagos al inicio y cobros al cierre de cada mes? Las fechas explícitas de permisos/cotizaciones se respetan. Si no representa tu caso, quedará pendiente validar fechas antes de clasificar.",
    }
    if step in questions:
        options = [("yes", "Sí, representa"), ("unknown", "No sé aún"), ("pause", "Pausar")] if step == "calendar" else [("pause", "Pausar")]
        if step == "representative":
            options = [("yes", "Sí, representa"), ("no", "Cambiar montos"), ("unknown", "No sé aún")]
        if step == "projection_date":
            options = [("yes", "Usar ese mes"), ("pause", "Pausar")]
        return _widget(context, questions[step], options)
    if step == "edit":
        return _widget(context, "¿Qué dato quieres cambiar? Conservaremos el resto y las versiones anteriores.",
                       [("edit_" + key, label) for key, label in _EDIT_FIELDS.items()], listing=True)
    if step == "review":
        return _widget(context, f"Revisa tu presupuesto ({str(d['period_start'])[:7]}):\n"
                       f"Ingresos: {_money(a.get('income_total'))}\nGastos del mes observado: {_money(a.get('expenses_total'))}\n"
                       f"De esos gastos, excepcionales: {_money(a.get('expense_oneoff'))}\n"
                       f"Ventas mensuales futuras: {_money(a.get('projected_income', a.get('income_total'))) if a.get('representative') else 'por confirmar'}\n"
                       f"Gastos mensuales futuros: {_money(a.get('projected_expenses', a.get('expenses_total'))) if a.get('representative') else 'por confirmar'}\n"
                       f"Efectivo: {_money(a.get('available_cash'))}\nRetiros/mes: {_money(a.get('owner_amount'))}\n"
                       f"Deudas/mes: {_money(a.get('debt_amount'))}\n"
                       "Se mostrará por separado el costo inicial y los pagos futuros. "
                       "La estimación de FinancIAl usa tus datos y supuestos aceptados. Los costos pendientes se mostrarán por separado. ¿Calculamos?",
                       [("confirm", "Guardar diagnóstico"), ("edit", "Cambiar datos"), ("pause", "Pausar")])
    return _result_widget(context)


def _result_widget(context):
    if cost_guide.is_guided(context):
        return presentation.summary(context, _widget, _money)
    d = context["diagnostic"]
    result = d.get("result") or {}
    label = d.get("capacity") or ("No evaluable" if d.get("assessment_status") == "not_evaluable" else "Parcial: faltan datos/validaciones")
    return _widget(context, f"📊 Diagnóstico: {label}\n"
                   f"Ingresos menos gastos del mes observado: {_money(result.get('current_operating_result'))}\n"
                   f"Resultado futuro conocido: {_money(result.get('future_known_operating_result'))}/mes\n"
                   f"Pagos iniciales pendientes conocidos: {_money(result.get('initial_remaining_max'))}\n"
                   f"Pendientes: {len(d.get('missing_fields') or [])}. Horizonte: tres meses.\n\n"
                   "Lo conocido excluye importes pendientes, NO significa costo total. "
                   "Reglas propuestas de FinancIAl; no es autorización ni evaluación crediticia. "
                   "Escribe 'menú' para volver.",
                   [("details", "Costos y balance"), ("sources", "Ver fuentes"), ("edit", "Cambiar datos")])


def _details(context, *, sources=False):
    if cost_guide.is_guided(context):
        return presentation.detail(context, _widget, _money, _missing_label, sources=sources)
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
    titles.update(estimate_catalog_expired="Revisar la vigencia del catálogo de estimaciones",
                  estimate_coverage="Revisar si faltan costos fuera de la lista",
                  estimate_needs="Completar las preguntas del negocio",
                  estimate_profile_changed="Revisar costos tras el cambio de rubro, comuna o lugar")
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


def _load_estimates():
    row = get_formalization_estimate_catalog()
    if row is None:
        row = get_formalization_estimate_catalog(version=DEFAULT_ESTIMATE_VERSION, include_inactive=True)
    if row is None:
        raise EstimateCatalogUnavailable("missing")
    if not row.get("review_by") or date.fromisoformat(str(row["review_by"])) < _today():
        raise EstimateCatalogUnavailable("expired")
    return row


def _new(user, catalog):
    if not catalog:
        raise EstimateCatalogUnavailable("missing")
    today = _today()
    end = today.replace(day=1)
    scope = lambda text: re.sub(r"[^a-z0-9]+", "_", normalize(text or "")).strip("_") or "no_definido"
    return save_formalization_draft(user["id"], str(uuid4()), 0, {
        "current_step": "location", "pending_field": "location", "conversation_status": "active",
        "period_start": month_after(end, -1), "period_end": end,
        "projection_start": month_after(end), "activity_code": scope(user.get("rubro")),
        "comuna_code": scope(user.get("comuna")), "rules_version": RULES_VERSION,
        "rules_config": dict(RULES_CONFIG),
        "answers": {"estimate_catalog": catalog},
    })


def _next(user, context, answers, *, items=None, completed_step=None):
    d = context["diagnostic"]
    completed_step = completed_step or d["current_step"]
    returning = answers.pop("return_to_review", False)
    step = "review" if returning else _SEQUENCE[_SEQUENCE.index(completed_step) + 1]
    if returning and completed_step in {"income", "expenses"}:
        step = "representative"
        answers["return_to_review"] = True
    context = _save(user, context, {"current_step": step, "pending_field": step, "answers": answers,
                                  "conversation_status": "active"}, items=items)
    return _prompt(context)


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
            if action == "guided_restart":
                entry = True
        if entry:
            if (normalize(raw) == "nuevo diagnostico" or action == "guided_restart") and d and d["workflow_status"] in {"draft", "ready", "confirmed"}:
                # Validate the new catalog before closing the resumable draft.
                catalog = _load_estimates()
                _save(user, context, {"workflow_status": "archived" if d["workflow_status"] == "confirmed" else "cancelled", "conversation_status": "closed"})
                stage = "create"
                context = _new(user, catalog)
            elif not d or d["workflow_status"] in {"cancelled", "archived"}:
                stage = "create"
                context = _new(user, _load_estimates())
            elif d["workflow_status"] == "confirmed":
                if normalize(raw) != "rehacer diagnostico":
                    return _result_widget(context)
                if not cost_guide.is_guided(context):
                    return _legacy_notice(context)
                context = _set_step(user, context, "edit")
            else:
                if not cost_guide.is_guided(context):
                    return _legacy_notice(context)
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
        if diagnostic_button and d.get("result") and cost_guide.is_guided(context):
            if action == "gsummary":
                return _result_widget(context)
            for prefix in ("gdetails_", "gsources_"):
                if action.startswith(prefix) and action[len(prefix):].isdigit():
                    return presentation.detail(context, _widget, _money, _missing_label,
                                               sources=prefix == "gsources_", page=int(action[len(prefix):]))
        if action == "edit" and diagnostic_button:
            if not cost_guide.is_guided(context):
                return _legacy_notice(context)
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
        word_actions = {
            "expenses": {"completo": "yes", "agregar": "expense_add", "categorias": "expense_categories"},
            "expense_oneoff": {"ninguno": "none", "no": "none"},
            "expense_categories": {"terminar gastos": "expense_done", **{normalize(label): "expense_cat_" + key for key, label in EXPENSE_LABELS.items()}},
            "expense_category": {"mantener": "expense_keep"},
            "income": {"completo": "yes", "agregar": "income_add", "detalle": "income_details"},
            "income_details": {"volver": "income_done"},
        }.get(step, {})
        word_answer = action in word_actions
        action = word_actions.get(action, action)
        if cost_guide.is_guided(context) and not diagnostic_button:
            guided_action = cost_guide.text_action(context, raw)
            if guided_action:
                action, word_answer = guided_action, True
        # Capture only expected structured answers. Everything else exits safely.
        valid = diagnostic_button or word_answer or action in {"yes", "no", "unknown"} or bool(re.fullmatch(r"[\d.,$+\-\s]+(?:pesos|clp)?", raw.lower()))
        valid = valid or "=" in raw or bool(re.fullmatch(r"\d{4}-\d{2}", raw)) or bool(re.fullmatch(r"total\s+[\d.$\s]+", raw.lower()))
        valid = valid or (step == "estimate_date" and bool(re.fullmatch(r"(?:\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})", raw)))
        if not valid:
            _save(user, context, {"conversation_status": "paused"})
            return PASS_TO_AI
        if not cost_guide.is_guided(context):
            return _legacy_notice(context)
        if cost_guide.is_guided(context) and step.startswith("estimate_"):
            patch, items = cost_guide.transition(context, action, raw, _amount, _today())
            return _prompt(_save(user, context, patch, items=items))
        if step == "edit" and action.startswith("edit_") and action[5:] in _EDIT_FIELDS:
            target = action[5:]
            if target == "refresh":
                clear_period_review(a)
                a["cost_page"] = 0
                scope = lambda text: re.sub(r"[^a-z0-9]+", "_", normalize(text or "")).strip("_") or "no_definido"
                context = _save(user, context, {"answers": a, "current_step": "expenses", "pending_field": "expenses",
                                               "conversation_status": "active", "activity_code": scope(user.get("rubro")),
                                               "comuna_code": scope(user.get("comuna"))}, refresh=True)
                return _prompt(_save_budgets(user, context))
            # Period changes invalidate the observed amounts, not private quotes.
            if target not in {"period", "costs", "projection_date"}:
                a["return_to_review"] = True
            if target == "costs" and cost_guide.is_guided(context):
                target = "estimate_costs" if a.get("estimate_decisions") else "estimate_needs"
            return _prompt(_set_step(user, context, target, a))
        if step == "location" and action in {"vivienda", "local", "otra"}:
            context = _set_step(user, context, "period", a, location_type=action)
            return _prompt(context)
        if step == "period":
            if action == "yes":
                return _next(user, context, a)
            start = date.fromisoformat(raw + "-01")
            if start >= _today().replace(day=1):
                raise ValueError("Selecciona un mes ya terminado para comparar datos completos.")
            clear_period_review(a)
            context = _set_step(user, context, "expenses", a, period_start=start, period_end=month_after(start))
            return _prompt(_save_budgets(user, context))
        if step == "expenses":
            financial = d["financial_snapshot"]
            if action in {"expense_add", "expense_categories", "no"}:
                a["expense_review"] = review_expenses(a, financial)
                return _prompt(_set_step(user, context, "expense_categories" if action == "no" else action, a))
            if action == "unknown":
                return _prompt(context)
            if action == "yes":
                a["expenses_breakdown"] = review_expenses(a, financial)
                a["expenses_total"] = sum(a["expenses_breakdown"].values())
            elif "=" in raw:
                # Accept the old format for users returning to an earlier prompt.
                a["expenses_breakdown"] = _amount_fields(raw, _EXPENSE_KEYS)
                a["expenses_total"] = sum(a["expenses_breakdown"].values())
                a["expense_review"] = {
                    "arriendo_servicios": a["expenses_breakdown"]["arriendo"] + a["expenses_breakdown"]["servicios"],
                    "insumos_mercaderia": a["expenses_breakdown"]["insumos"],
                    "transporte": a["expenses_breakdown"]["transporte"], "otros_gastos": a["expenses_breakdown"]["otros"],
                }
            else:
                a["expense_review"] = {"sin_desglose": _amount(raw)}
                return _prompt(_set_step(user, context, "expenses", a))
            a["expenses_confirmed"] = True
            a.pop("expense_oneoff", None)
            a["expense_oneoff_confirmed"] = False
            invalidate_projection(a)
            return _prompt(_set_step(user, context, "expense_oneoff", a))
        if step == "expense_add":
            amounts = review_expenses(a, d["financial_snapshot"])
            amounts["sin_desglose"] = amounts.get("sin_desglose", 0) + _amount(raw)
            a["expense_review"] = amounts
            return _prompt(_set_step(user, context, "expenses", a))
        if step == "expense_categories":
            if action == "expense_done":
                return _prompt(_set_step(user, context, "expenses", a))
            if action.startswith("expense_cat_") and action.removeprefix("expense_cat_") in EXPENSE_LABELS:
                a["expense_category"] = action.removeprefix("expense_cat_")
                return _prompt(_set_step(user, context, "expense_category", a))
        if step == "expense_category":
            amounts = review_expenses(a, d["financial_snapshot"])
            key = a["expense_category"]
            if action != "expense_keep":
                amounts[key] = _amount(raw)
            a["expense_review"] = amounts
            return _prompt(_set_step(user, context, "expense_categories", a))
        if step == "expense_oneoff":
            if action == "unknown":
                a.pop("expense_oneoff", None)
                a["expense_oneoff_confirmed"] = False
            else:
                amount = 0 if action == "none" else _amount(raw)
                if amount > a["expenses_total"]:
                    return _widget(context, "El gasto excepcional no puede superar el total del mes.\n\n" + _prompt(context)["body"],
                                   [("none", "Ninguno"), ("unknown", "No sé aún"), ("pause", "Pausar")])
                a.update(expense_oneoff=amount, expense_oneoff_confirmed=True)
            return _next(user, context, a, completed_step="expenses")
        if step == "income":
            base = a.get("income_review", a.get("income_total", snapshot_amount(d["financial_snapshot"].get("operating_income_total"))))
            if action in {"income_add", "income_details", "no"}:
                a["income_review"] = base
                return _prompt(_set_step(user, context, "income_details" if action == "no" else action, a))
            if action == "unknown":
                return _prompt(context)
            a["income_total"] = base if action == "yes" else (_amount(raw[6:]) if raw.lower().startswith("total ") else base + _amount(raw))
            a["income_review"] = a["income_total"]
            a["income_confirmed"] = True
            invalidate_projection(a)
            return _next(user, context, a)
        if step in {"income_add", "income_details"}:
            if action != "income_done":
                amount = _amount(raw)
                a["income_review"] = a["income_review"] + amount if step == "income_add" else amount
            return _prompt(_set_step(user, context, "income", a))
        if step in {"cash", "owner", "debt", "preparation"}:
            key = {"cash": "available_cash", "owner": "owner_amount", "debt": "debt_amount", "preparation": "preparation_amount"}[step]
            amount = None if action == "unknown" else _amount(raw)
            if cost_guide.is_guided(context) and step == "owner" and amount is not None and amount > a.get("income_total", 0):
                a["pending_owner"] = amount
                return _prompt(_set_step(user, context, "confirm_owner", a))
            a[key] = amount
            if a[key] is None:
                a.pop(key)
            if step != "cash":
                a[{"owner": "owner_confirmed", "debt": "debt_confirmed", "preparation": "preparation_confirmed"}[step]] = action != "unknown"
            else:
                a["cash_as_of"] = _today().isoformat()
            return _next(user, context, a)
        if step == "confirm_owner" and action in {"yes", "no"}:
            amount = a.pop("pending_owner")
            if action == "no":
                return _prompt(_set_step(user, context, "owner", a))
            a.update(owner_amount=amount, owner_confirmed=True)
            return _next(user, context, a, completed_step="owner")
        if step == "representative":
            if action == "no" or (action == "yes" and a.get("expense_oneoff_confirmed") is False):
                return _prompt(_set_step(user, context, "projection_income", a))
            if action in {"yes", "unknown"}:
                a["representative"] = action == "yes"
                a.pop("projected_income", None)
                a.pop("projected_expenses", None)
                if action == "yes" and a.get("expense_oneoff_confirmed") and a.get("expense_oneoff"):
                    a["projected_expenses"] = a["expenses_total"] - a["expense_oneoff"]
            else:
                fields = _amount_fields(raw, ("ingresos", "gastos"))
                a.update(projected_income=fields["ingresos"], projected_expenses=fields["gastos"], representative=True)
            return _next(user, context, a)
        if step == "projection_income":
            a["projection_income_draft"] = _amount(raw)
            return _prompt(_set_step(user, context, "projection_expenses", a))
        if step == "projection_expenses":
            a.update(projected_income=a.pop("projection_income_draft"), projected_expenses=_amount(raw), representative=True)
            return _next(user, context, a, completed_step="representative")
        if step == "projection_date":
            start = date.fromisoformat(d["projection_start"]) if action == "yes" else date.fromisoformat(raw + "-01")
            if start <= _today():
                raise ValueError("Selecciona el primer día de un mes futuro.")
            return _prompt(_set_step(user, context, "preparation", a, projection_start=start))
        if step == "calendar" and action in {"yes", "no", "unknown"}:
            a["calendar_confirmed"] = action == "yes"
            target = "estimate_costs" if a.get("estimate_decisions") else "estimate_needs"
            context = _set_step(user, context, target, a)
            return _prompt(_save_budgets(user, context))
        if step == "review" and action == "confirm":
            if cost_guide.is_guided(context):
                a["budget_as_of"] = _today().isoformat()
                context = _save(user, context, {"answers": a})
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
    except EstimateCatalogUnavailable:
        return ("No está disponible un catálogo vigente para el diagnóstico guiado. "
                "El equipo debe revisar el catálogo de estimaciones en Supabase. "
                "No modifiqué tus datos ni volveré al formulario técnico.\n\n"
                "Código: DIAG_CATALOG")
    except cost_guide.GuideInputError as error:
        prompt = _prompt(context)
        return {**prompt, "body": str(error) + "\n\n" + prompt["body"]}
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
