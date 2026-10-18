"""Selección determinista de estimaciones para el diagnóstico económico.

Prepara candidatos para el flujo guiado; no decide si un permiso es legalmente
exigible ni guarda partidas por su cuenta.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Any


_SUBTYPES = {
    "alimentos": {"alimentos_preparacion", "alimentos_envasados"},
    "textil": {"textil_confeccion", "textil_venta"},
}
_MAX_CLP = 9_000_000_000_000


def _slug(value: str | None) -> str:
    normalized = unicodedata.normalize("NFD", str(value or "").lower())
    plain = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9]+", "_", plain).strip("_")


def _money(value: Any, label: str, *, allow_zero: bool = True) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label} debe ser un monto confirmado")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{label} debe ser un número") from error
    if not number.is_finite() or number < 0 or (not allow_zero and number == 0):
        raise ValueError(f"{label} debe ser positivo y finito")
    rounded = int(number.to_integral_value(rounding=ROUND_CEILING))
    if rounded > _MAX_CLP:
        raise ValueError(f"{label} excede el límite del diagnóstico")
    return rounded


def _range(variant: dict) -> tuple[int, int]:
    low = _money(variant.get("min"), "Mínimo")
    high = _money(variant.get("max"), "Máximo")
    if variant.get("unit") != "CLP" or high < low:
        raise ValueError("Rango del catálogo inválido")
    return low, high


def _decision(cost: dict, status: str, *, variant: dict | None = None,
              amount: tuple[int, int] | None = None, reason: str = "",
              missing: str | None = None, details: dict | None = None) -> dict:
    return {
        "code": cost["code"], "title": cost["title"], "status": status,
        "variant_id": variant["id"] if variant else None,
        "amount_clp_min": amount[0] if amount else None,
        "amount_clp_max": amount[1] if amount else None,
        "frequency": cost["frequency"],
        "timing": cost["timing"],
        "basis": variant.get("basis") if variant else None,
        "source_ids": list(cost.get("sources") or []),
        "reason": reason, "missing": missing, "details": details or {},
    }


def _selected(cost: dict, variant_id: str, *, amount: tuple[int, int] | None = None,
              details: dict | None = None) -> dict:
    variant = next((row for row in cost["variants"] if row["id"] == variant_id), None)
    if variant is None:
        raise ValueError(f"Falta la variante {cost['code']}:{variant_id}")
    if amount is None:
        if variant["method"] not in {"fixed", "range"}:
            raise ValueError(f"La variante {cost['code']}:{variant_id} requiere cálculo")
        amount = _range(variant)
    return _decision(cost, "ready", variant=variant, amount=amount, details=details)


def _equipment(cost: dict, document: dict, subtype: str, selected: Any) -> dict:
    if selected is None:
        return _decision(cost, "pending", missing="missing_components",
                         reason="Falta identificar qué equipos necesita comprar.")
    if isinstance(selected, list):
        if any(not isinstance(name, str) for name in selected):
            raise ValueError("Los equipos deben indicarse por identificador")
        if len(selected) != len(set(selected)):
            raise ValueError("Un equipo aparece más de una vez")
        quantities = {name: 1 for name in selected}
    elif isinstance(selected, dict):
        quantities = selected
    else:
        raise ValueError("Los equipos deben indicarse por identificador")
    if not quantities:
        return _decision(cost, "not_applicable", reason="No faltan equipos según el usuario.")
    components = {row["id"]: row for row in document["components"]}
    allowed_prefixes = {
        "alimentos_preparacion": ("EQ_AL_", "EQ_SEGURIDAD"),
        "alimentos_envasados": ("EQ_AL_FRIO", "EQ_VENTA_", "EQ_SEGURIDAD"),
        "textil_confeccion": ("EQ_TX_", "EQ_SEGURIDAD"),
        "textil_venta": ("EQ_VENTA_", "EQ_SEGURIDAD"),
    }[subtype]
    low = high = 0
    chosen = []
    for key, quantity in quantities.items():
        if not isinstance(key, str) or key not in components or not key.startswith(allowed_prefixes):
            raise ValueError(f"Equipo no compatible con esta actividad: {key}")
        if type(quantity) is not int or not 1 <= quantity <= 10:
            raise ValueError("Indica de 1 a 10 unidades por equipo")
        item_low, item_high = _range(components[key])
        low += item_low * quantity
        high += item_high * quantity
        chosen.append({"id": key, "quantity": quantity, "min": item_low, "max": item_high})
    if high > _MAX_CLP:
        raise ValueError("El equipamiento excede el límite del diagnóstico")
    return _selected(cost, "componentes", amount=(low, high),
                     details={"components": chosen, "package_added": False})


def _municipal_patent(cost: dict, answers: dict) -> dict:
    required = ("capital_base_min", "capital_base_max", "dated_utm")
    missing = next((key for key in required if answers.get(key) is None), None)
    if missing:
        return _decision(cost, "pending", missing=missing,
                         reason="Faltan capital y UTM fechada para explorar un rango anual.")
    capital_low = _money(answers["capital_base_min"], "Capital mínimo")
    capital_high = _money(answers["capital_base_max"], "Capital máximo")
    utm = _money(answers["dated_utm"], "UTM", allow_zero=False)
    if capital_high < capital_low:
        raise ValueError("El capital máximo no puede ser menor al mínimo")
    floor, ceiling = utm, 8000 * utm
    low = min(max((Decimal(capital_low) * Decimal("0.0025")).to_integral_value(
        rounding=ROUND_CEILING), floor), ceiling)
    high = min(max((Decimal(capital_high) * Decimal("0.005")).to_integral_value(
        rounding=ROUND_CEILING), floor), ceiling)
    if high > _MAX_CLP:
        raise ValueError("La patente estimada excede el límite del diagnóstico")
    return _selected(cost, "rango_marco_general", amount=(int(low), int(high)),
                     details={"period": "annual", "cash_dates_confirmed": False,
                              "municipal_rate_confirmed": False})


def select_cost_estimates(document: dict, profile: dict, answers: dict) -> dict:
    """Propone costos del catálogo; no convierte desconocido en cero.

    `answers` contiene hechos confirmados o hipótesis aceptadas. Ningún
    componente, permiso, ruta MEF o gasto mensual se presume por rubro.
    La salida es un subtotal conocido, no el costo total de formalizarse.
    """
    if not isinstance(document, dict) or not isinstance(profile, dict) or not isinstance(answers, dict):
        raise ValueError("El catálogo, perfil y respuestas deben ser objetos")
    if not isinstance(document.get("costs"), list) or not isinstance(document.get("components"), list):
        raise ValueError("Catálogo de estimaciones incompleto")
    activity = _slug(profile.get("rubro") or profile.get("activity_code")).removeprefix("rubro_")
    commune = _slug(profile.get("comuna") or profile.get("comuna_code"))
    location = _slug(profile.get("location_type") or answers.get("location_type"))
    subtype = _slug(answers.get("subtype"))
    scope = document.get("scope") or {}
    if activity not in _SUBTYPES or commune not in scope.get("communes", []) or location not in scope.get("locations", []):
        raise ValueError("Actividad, comuna o lugar fuera del catálogo piloto")
    if subtype and subtype not in _SUBTYPES[activity]:
        raise ValueError("La actividad específica no corresponde al rubro")

    scenarios = [row for row in document.get("scenarios", [])
                 if (row.get("activity"), row.get("subtype"), row.get("location"), row.get("commune"))
                 == (activity, subtype, location, commune)]
    if len(scenarios) > 1:
        raise ValueError("Escenarios duplicados para este perfil")
    scenario = scenarios[0] if scenarios else None
    packages = {row["id"]: row for row in document.get("packages", [])}
    suggested_package = (scenario or {}).get("package")
    if not suggested_package and subtype:
        suggested_package = ("VENTA_BASICO" if subtype.endswith("_venta") or
                             subtype == "alimentos_envasados" else None)
    if suggested_package and suggested_package not in packages:
        raise ValueError("El paquete sugerido no existe en el catálogo")

    costs = {cost["code"]: cost for cost in document["costs"]}
    if len(costs) != len(document["costs"]):
        raise ValueError("Hay conceptos duplicados en el catálogo")
    required_codes = {"C01", "C07", "C08", "C09", "C12", "C13", "C16", "C17", "C24"}
    if not required_codes.issubset(costs):
        raise ValueError("Faltan conceptos esenciales del diagnóstico")
    decisions = {code: _decision(cost, "deferred", reason="Se evaluará si el usuario describe esta necesidad.")
                 for code, cost in costs.items()}

    sii = _slug(profile.get("inicio_sii"))
    if sii == "no":
        decisions["C01"] = _selected(costs["C01"], "directo")
    elif sii == "si":
        decisions["C01"] = _decision(costs["C01"], "not_applicable",
                                      reason="El inicio de actividades ya consta en el perfil.")
    else:
        decisions["C01"] = _decision(costs["C01"], "pending", missing="inicio_sii")

    if not subtype:
        for code in ("C07" if activity == "alimentos" else "C08", "C12", "C13", "C24"):
            decisions[code] = _decision(costs[code], "pending", missing="subtype")
    elif activity == "alimentos":
        food = costs["C07"]
        if subtype == "alimentos_preparacion":
            if location == "vivienda" and answers.get("mef_route") == "accepted_hypothesis":
                if (answers.get("capital_under_10m") is True and
                        answers.get("small_installation") is True):
                    decisions["C07"] = _selected(food, "mef_reserva",
                                                 details={"mef_eligibility_confirmed": False})
                else:
                    decisions["C07"] = _decision(food, "pending",
                                                 missing="small_installation_and_capital")
            elif answers.get("mef_route") == "not_applicable" or location == "local":
                if answers.get("small_installation") is True and answers.get("capital_under_10m") is True:
                    decisions["C07"] = _selected(food, "preparacion_pequena")
                else:
                    decisions["C07"] = _decision(food, "pending", missing="small_installation_and_capital")
            else:
                decisions["C07"] = _decision(food, "pending", missing="mef_route",
                                             reason="La ruta sanitaria de la vivienda debe revisarse.")
        elif (answers.get("needs_cold_storage") is False and answers.get("small_installation") is True
              and answers.get("capital_under_10m") is True):
            decisions["C07"] = _selected(food, "venta_envasados_sin_frio")
        else:
            decisions["C07"] = _decision(food, "pending", missing="food_storage_and_scale")
        decisions["C08"] = _decision(costs["C08"], "not_applicable",
                                      reason="El informe de taller textil no corresponde a este rubro.")
    else:
        decisions["C07"] = _decision(costs["C07"], "not_applicable",
                                      reason="La autorización de alimentos no corresponde a este rubro.")
        technical = answers.get("technical_review")
        if subtype == "textil_venta" or technical == "not_required":
            decisions["C08"] = _decision(costs["C08"], "not_applicable",
                                          reason="No se ha identificado una prestación técnica de taller.")
        elif technical == "report" and answers.get("capital_under_10m") is True:
            decisions["C08"] = _selected(costs["C08"], "informe_reserva")
        elif technical == "qualification":
            decisions["C08"] = _selected(costs["C08"], "calificacion_referencia")
        else:
            decisions["C08"] = _decision(costs["C08"], "pending", missing="technical_review",
                                          reason="Revisar si se exige informe o calificación; no sumar ambos.")

    docs = answers.get("missing_property_documents")
    if docs is True:
        decisions["C09"] = _selected(costs["C09"], "documentacion_simple")
    elif docs is False:
        decisions["C09"] = _decision(costs["C09"], "not_applicable",
                                      reason="No faltan documentos del inmueble según el usuario.")
    else:
        decisions["C09"] = _decision(costs["C09"], "pending",
                                      missing="missing_property_documents")

    if subtype:
        adaptations = answers.get("adaptations")
        if adaptations == "minor":
            variant_id = ("venta_simple" if subtype in {"alimentos_envasados", "textil_venta"}
                          else f"{'al' if activity == 'alimentos' else 'tx'}_{location}")
            decisions["C12"] = _selected(costs["C12"], variant_id)
        elif adaptations == "none":
            decisions["C12"] = _decision(costs["C12"], "not_applicable",
                                          reason="El usuario indica que no necesita arreglos.")
        else:
            decisions["C12"] = _decision(costs["C12"], "pending", missing="adaptations",
                                          reason="Las obras mayores quedan fuera de estos rangos.")
        decisions["C13"] = _equipment(costs["C13"], document, subtype,
                                       answers.get("missing_components"))
        operation_change = answers.get("new_operating_costs_not_in_baseline")
        if operation_change is True:
            if subtype in {"alimentos_envasados", "textil_venta"}:
                decisions["C24"] = _decision(costs["C24"], "pending",
                                              missing="operation_cost_quote",
                                              reason="No hay un rango específico para la venta simple.")
            else:
                variant_id = f"{'al' if activity == 'alimentos' else 'tx'}_{location}"
                decisions["C24"] = _selected(costs["C24"], variant_id,
                                              details={"meaning": "increment_not_future_total"})
        elif operation_change is False:
            decisions["C24"] = _decision(costs["C24"], "not_applicable",
                                          reason="No hay incremento operativo adicional.")
        else:
            decisions["C24"] = _decision(costs["C24"], "pending",
                                          missing="new_operating_costs_not_in_baseline")

    if answers.get("municipal_patent_required") is False:
        decisions["C16"] = _decision(costs["C16"], "not_applicable",
                                      reason="Aplicabilidad declarada; requiere verificar el caso.")
    elif answers.get("municipal_patent_required") is True:
        decisions["C16"] = _municipal_patent(costs["C16"], answers)
    else:
        decisions["C16"] = _decision(costs["C16"], "pending", missing="municipal_patent_required")

    if answers.get("waste_charge_required") is False or answers.get("waste_charge_uncovered") is False:
        decisions["C17"] = _decision(costs["C17"], "not_applicable",
                                      reason="El usuario ya cubre este cobro o no le corresponde.")
    elif answers.get("waste_charge_required") is True and answers.get("waste_charge_uncovered") is True:
        decisions["C17"] = _selected(costs["C17"], "reserva_aseo")
    else:
        decisions["C17"] = _decision(costs["C17"], "pending",
                                      missing="waste_charge_required_and_uncovered")

    ordered = [decisions[cost["code"]] for cost in document["costs"]]
    initial = [row for row in ordered if row["status"] == "ready" and
               row["timing"] == "initial" and row["amount_clp_min"] is not None]
    return {
        "catalog_version": document.get("version"),
        "scenario_id": scenario["id"] if scenario else None,
        "suggested_package": suggested_package,
        "suggested_components": list(packages[suggested_package]["components"])
            if suggested_package else [],
        "decisions": ordered,
        "known_initial_min": sum(row["amount_clp_min"] for row in initial),
        "known_initial_max": sum(row["amount_clp_max"] for row in initial),
        "is_total": False,
    }
