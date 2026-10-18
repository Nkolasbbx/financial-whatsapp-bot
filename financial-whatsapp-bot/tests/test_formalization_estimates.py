"""Selección de costos con respuestas ficticias; no consulta Supabase."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.formalization_estimates import select_cost_estimates
from db import formalization_diagnostics as diagnostic_db


ROOT = Path(__file__).resolve().parents[2]
SQL_SEED = ROOT / "supabase" / "migrations" / "20261017_formalization_estimate_catalog.sql"


def real_catalog():
    """La migración está ignorada por Git; usarla solo en la prueba local."""
    if not SQL_SEED.is_file():
        raise unittest.SkipTest("El SQL de prueba local no está en este checkout")
    match = re.search(r"\$estimate_document\$(.*?)\$estimate_document\$",
                      SQL_SEED.read_text(encoding="utf-8"), re.DOTALL)
    if not match:
        raise AssertionError("La migración no contiene el catálogo")
    return json.loads(match.group(1))


def small_catalog():
    def cost(code, variants, timing="initial", frequency="once"):
        return {"code": code, "title": code, "variants": variants, "timing": timing,
                "frequency": frequency, "sources": ["A01"]}

    def variant(name, low, high):
        return {"id": name, "method": "range", "min": low, "max": high,
                "unit": "CLP", "basis": "financial_planning_assumption"}

    return {
        "version": "test-v1",
        "scope": {"communes": ["recoleta", "el_bosque"], "locations": ["local", "vivienda"]},
        "components": [
            {"id": "EQ_AL_MESA", "min": 100, "max": 200, "unit": "CLP"},
            {"id": "EQ_TX_RECTA", "min": 300, "max": 400, "unit": "CLP"},
        ],
        "packages": [{"id": "AL_BASICO", "components": ["EQ_AL_MESA"]},
                     {"id": "TX_BASICO", "components": ["EQ_TX_RECTA"]},
                     {"id": "VENTA_BASICO", "components": []}],
        "scenarios": [{"id": "AL-L-REC", "activity": "alimentos",
                       "subtype": "alimentos_preparacion", "location": "local",
                       "commune": "recoleta", "package": "AL_BASICO"}],
        "costs": [
            cost("C01", [variant("directo", 0, 0)]),
            cost("C07", [variant("mef_reserva", 10, 20),
                          variant("preparacion_pequena", 30, 40),
                          variant("venta_envasados_sin_frio", 15, 25)]),
            cost("C08", [variant("informe_reserva", 20, 30),
                          variant("calificacion_referencia", 25, 25)]),
            cost("C09", [variant("documentacion_simple", 5, 10)]),
            cost("C12", [variant("al_local", 50, 100), variant("al_vivienda", 20, 40),
                          variant("tx_local", 25, 50), variant("tx_vivienda", 10, 20),
                          variant("venta_simple", 10, 30)]),
            cost("C13", [{"id": "componentes", "method": "component_sum",
                           "basis": "financial_estimate"}]),
            cost("C16", [{"id": "rango_marco_general", "method": "formula",
                           "basis": "official_framework_financial_scenario"}],
                 timing="recurring", frequency="annual"),
            cost("C17", [variant("reserva_aseo", 10, 20)],
                 timing="recurring", frequency="annual"),
            cost("C24", [variant("al_local", 5, 10), variant("al_vivienda", 3, 6),
                          variant("tx_local", 4, 8), variant("tx_vivienda", 2, 5)],
                 timing="recurring", frequency="monthly"),
        ],
    }


def by_code(result, code):
    return next(row for row in result["decisions"] if row["code"] == code)


class EstimateSelectionTests(unittest.TestCase):
    def test_supabase_reader_uses_one_request_and_requires_active_by_default(self):
        client = MagicMock()
        query = client.table.return_value.select.return_value
        query.eq.return_value = query
        query.limit.return_value = query
        query.execute.return_value.data = [{
            "id": "test-id", "version": "test-v1", "document": {"version": "test-v1"},
            "active": True,
        }]
        with patch.object(diagnostic_db, "_admin_client", return_value=client):
            record = diagnostic_db.get_formalization_estimate_catalog()
        self.assertEqual(record["version"], "test-v1")
        client.table.assert_called_once_with("formalization_estimate_catalog_versions")
        query.eq.assert_called_once_with("active", True)
        query.execute.assert_called_once()

    def test_unknown_costs_do_not_become_zero_or_legal_requirements(self):
        result = select_cost_estimates(
            small_catalog(),
            {"rubro": "rubro_alimentos", "comuna": "Recoleta",
             "location_type": "local", "inicio_sii": "no"},
            {"subtype": "alimentos_preparacion"},
        )
        self.assertEqual(result["known_initial_min"], 0)
        self.assertEqual(by_code(result, "C07")["status"], "pending")
        self.assertEqual(by_code(result, "C12")["status"], "pending")
        self.assertEqual(by_code(result, "C13")["status"], "pending")
        self.assertFalse(result["is_total"])

    def test_only_confirmed_missing_equipment_is_counted(self):
        base = {"subtype": "alimentos_preparacion", "small_installation": True,
                "capital_under_10m": True, "missing_property_documents": True,
                "adaptations": "minor", "missing_components": ["EQ_AL_MESA"]}
        profile = {"rubro": "Alimentos", "comuna": "Recoleta", "location_type": "local",
                   "inicio_sii": "no"}
        result = select_cost_estimates(small_catalog(), profile, base)
        self.assertEqual((result["known_initial_min"], result["known_initial_max"]), (185, 350))
        self.assertEqual(by_code(result, "C13")["details"]["package_added"], False)
        without = select_cost_estimates(small_catalog(), profile,
                                        {**base, "missing_components": []})
        self.assertEqual(by_code(without, "C13")["status"], "not_applicable")
        self.assertEqual((without["known_initial_min"], without["known_initial_max"]), (85, 150))
        with self.assertRaisesRegex(ValueError, "no compatible"):
            select_cost_estimates(small_catalog(), profile,
                                  {**base, "missing_components": ["EQ_TX_RECTA"]})

    def test_patent_formula_is_annual_and_needs_capital_and_dated_utm(self):
        profile = {"rubro": "textil", "comuna": "El Bosque",
                   "location_type": "vivienda", "inicio_sii": "si"}
        answers = {"subtype": "textil_confeccion", "technical_review": "not_required",
                   "municipal_patent_required": True,
                   "capital_base_min": 1_000_000, "capital_base_max": 10_000_000,
                   "dated_utm": 72_151}
        result = select_cost_estimates(small_catalog(), profile, answers)
        patent = by_code(result, "C16")
        self.assertEqual((patent["amount_clp_min"], patent["amount_clp_max"]), (72_151, 72_151))
        self.assertEqual(patent["details"]["period"], "annual")
        self.assertEqual(by_code(result, "C01")["status"], "not_applicable")
        self.assertEqual(by_code(result, "C08")["status"], "not_applicable")
        with self.assertRaisesRegex(ValueError, "capital máximo"):
            select_cost_estimates(small_catalog(), profile,
                                  {**answers, "capital_base_max": 999_999})

    def test_unknown_subtype_and_bad_component_quantities(self):
        profile = {"rubro": "alimentos", "comuna": "Recoleta", "location_type": "local"}
        unknown = select_cost_estimates(small_catalog(), profile, {})
        self.assertEqual(by_code(unknown, "C07")["missing"], "subtype")
        with self.assertRaisesRegex(ValueError, "1 a 10"):
            select_cost_estimates(small_catalog(), profile,
                                  {"subtype": "alimentos_preparacion",
                                   "missing_components": {"EQ_AL_MESA": 0}})

    def test_eight_documented_scenarios_keep_separate_conditional_costs(self):
        catalog = real_catalog()
        expected = {"AL-V-REC": (475000, 1265000), "AL-V-BOS": (475000, 1265000),
                    "AL-L-REC": (890000, 2330000), "AL-L-BOS": (890000, 2330000),
                    "TX-V-REC": (410000, 940000), "TX-V-BOS": (410000, 940000),
                    "TX-L-REC": (510000, 1240000), "TX-L-BOS": (510000, 1240000)}
        for scenario in catalog["scenarios"]:
            with self.subTest(scenario=scenario["id"]):
                profile = {"rubro": "rubro_" + scenario["activity"],
                           "comuna": scenario["commune"],
                           "location_type": scenario["location"], "inicio_sii": "no"}
                answers = {"subtype": scenario["subtype"], "capital_under_10m": True,
                           "small_installation": True, "missing_property_documents": True,
                           "adaptations": "minor", "mef_route": "accepted_hypothesis",
                           "missing_components": list(next(row["components"] for row in catalog["packages"]
                                                           if row["id"] == scenario["package"]))}
                result = select_cost_estimates(catalog, profile, answers)
                self.assertEqual(result["scenario_id"], scenario["id"])
                self.assertEqual((result["known_initial_min"], result["known_initial_max"]),
                                 expected[scenario["id"]])
                self.assertFalse(result["is_total"])
                self.assertEqual(by_code(result, "C08")["status"],
                                 "pending" if scenario["activity"] == "textil" else "not_applicable")

    def test_retail_variants_do_not_add_workshop_equipment(self):
        catalog = real_catalog()
        package = next(row["components"] for row in catalog["packages"]
                       if row["id"] == "VENTA_BASICO")
        for activity, subtype, expected in (
            ("alimentos", "alimentos_envasados", (310000, 940000)),
            ("textil", "textil_venta", (260000, 840000)),
        ):
            with self.subTest(subtype=subtype):
                result = select_cost_estimates(
                    catalog,
                    {"rubro": activity, "comuna": "Recoleta",
                     "location_type": "local", "inicio_sii": "no"},
                    {"subtype": subtype, "small_installation": True, "capital_under_10m": True,
                     "needs_cold_storage": False, "missing_property_documents": True,
                     "adaptations": "minor", "missing_components": package},
                )
                self.assertEqual(result["suggested_package"], "VENTA_BASICO")
                self.assertEqual((result["known_initial_min"], result["known_initial_max"]), expected)
                self.assertEqual(by_code(result, "C12")["variant_id"], "venta_simple")
                if activity == "alimentos":
                    self.assertEqual(by_code(result, "C07")["variant_id"],
                                     "venta_envasados_sin_frio")
                else:
                    self.assertEqual(by_code(result, "C08")["status"], "not_applicable")


if __name__ == "__main__":
    unittest.main()
