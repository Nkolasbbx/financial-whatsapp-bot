"""Presupuesto guiado con catálogo ficticio y repositorio en memoria."""
import copy
import unittest
from datetime import date
from unittest.mock import patch

from core import formalization_cost_guide as guide
from core import formalization_flow as flow
from core.formalization_engine import evaluate_diagnostic
from tests import test_formalization_flow as helpers
from tests.test_formalization_estimates import small_catalog, real_catalog


def catalog_row():
    doc = small_catalog()
    doc["disclosure"] = {"text": "Los costos aproximados son estimaciones de FinancIAl, no cobros oficiales. Puedes reemplazarlos por tus montos."}
    doc["source_details"] = {"A01": {"organism": "FinancIAl", "scope": "Datos ficticios para pruebas"}}
    doc["costs"].extend([
        {"code": "C21", "title": "Apoyo contable", "frequency": "split", "timing": "mixed", "sources": ["A01"],
         "variants": [{"id": key, "method": "range", "unit": "CLP", "min": 100, "max": 200, "includes": "Servicio opcional"}
                      for key in ("apoyo_mensual_basico", "renta_anual_separada", "ayuda_inicial")]},
        {"code": "C23", "title": "Trabajadores", "frequency": "monthly", "timing": "recurring", "sources": ["A01"],
         "variants": [{"id": "trabajador", "method": "range", "unit": "CLP", "min": 1, "max": 2}]},
    ])
    return {"id": "33333333-3333-4333-8333-333333333333", "version": doc["version"],
            "author": "FinancIAl", "document": doc, "active": False,
            "prepared_at": "2026-10-17", "review_by": "2026-11-16"}


class GuidedFlowTests(unittest.TestCase):
    send = helpers.FlowTests.send
    button = helpers.FlowTests.button
    start = helpers.FlowTests.start

    def setUp(self):
        helpers.FlowTests.setUp(self)
        self.catalog = catalog_row()
        mock = patch.object(flow, "_load_estimates", side_effect=lambda: copy.deepcopy(self.catalog))
        self.loader = mock.start()
        self.addCleanup(mock.stop)

    def to_needs(self):
        self.start()
        for text in ("sí", "0", "sí", "1500000", "100000", "0", "sí", "2026-11", "0", "sí"):
            self.send(text)
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "estimate_needs")

    def to_costs(self):
        self.to_needs()
        facts = {"subtype": "alimentos_preparacion", "small_installation": True, "capital_under_10m": True,
                 "missing_property_documents": False, "adaptations": "minor", "new_operating_costs_not_in_baseline": True,
                 "municipal_patent_required": True, "waste_charge_required": False}
        for _ in range(30):
            q = guide.pending_question(self.repo.data)
            if not q:
                break
            key, _, options = q
            if options:
                value = facts.get(key)
                index = next(i for i, (candidate, _) in enumerate(options) if candidate == value)
                self.button("gchoice_" + str(index))
            else:
                self.send("1")
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "estimate_costs")

    def estimate(self, key, *, included=False):
        self.button("gcost_" + key)
        self.button("gaccept")
        step = self.repo.data["diagnostic"]["current_step"]
        if step == "estimate_paid":
            self.send("0")
            self.button("gassume")
        elif step == "estimate_included":
            self.button("yes" if included else "no")

    def test_disclosure_before_question_and_single_catalog_load(self):
        result = self.send("diagnóstico")
        self.assertIn("estimaciones de FinancIAl", result["body"])
        self.assertIn("Alimentos", result["body"])
        self.button("local")
        self.button("yes")
        self.send("¿Cómo postulo a un fondo?")
        self.send("retomar diagnóstico")
        self.loader.assert_called_once()
        self.assertEqual(self.repo.data["diagnostic"]["answers"]["estimate_catalog"]["version"], "test-v1")

    def test_new_diagnosis_uses_new_id_and_preserves_draft_if_catalog_fails(self):
        self.to_costs()
        before = copy.deepcopy(self.repo.data)
        with patch.object(flow, "_load_estimates", side_effect=ValueError("invalid catalog")):
            self.send("nuevo diagnóstico")
        self.assertEqual(self.repo.data, before)
        result = self.send("nuevo diagnóstico")
        self.assertIn("estimaciones de FinancIAl", result["body"])
        self.assertNotEqual(self.repo.data["diagnostic"]["id"], before["diagnostic"]["id"])
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "location")
        self.assertEqual(self.repo.data["items"], [])

    def test_estimated_items_survive_budget_save_and_capture_version_sources(self):
        self.to_costs()
        self.estimate("C12")
        before = copy.deepcopy(guide.guide_items(self.repo.data))
        flow._save_budgets(helpers.USER, self.repo.get())
        self.assertEqual(guide.guide_items(self.repo.data), before)
        item = next(i for i in before if i["item_key"] == "estimate:C12")
        self.assertEqual(item["evidence"]["catalog_version"], "test-v1")
        self.assertEqual(item["metadata"]["origin"], "FinancIAl")
        self.assertTrue(item["metadata"]["assumed_date"])
        self.assertEqual(item["due_date"], "2026-10-31")

    def test_real_user_quote_replaces_estimate_and_deducts_paid_once(self):
        self.to_costs()
        self.estimate("C12")
        self.button("gcost_C12")
        self.button("gmanual")
        self.send("50000")
        self.button("gquote")
        self.send("10000")
        self.send("25/10/2026")
        self.button("gdone")
        self.button("yes")
        self.button("confirm")
        result = self.repo.revisions[-1]["result"]
        self.assertEqual(result["initial_remaining_max"], 40000)
        self.assertIsNone(result["economic_assessment"]["capacity"])
        item = next(i for i in self.repo.data["items"] if i["item_key"] == "estimate:C12")
        self.assertEqual(item["amount_state"], "quoted")
        self.assertFalse(item["metadata"]["assumed_date"])

    def test_abandoned_edit_keeps_previous_quote_and_other_questions_pause(self):
        self.to_costs()
        self.estimate("C12")
        before = copy.deepcopy(guide.guide_items(self.repo.data))
        self.button("gcost_C12")
        self.send("tengo otro monto")
        self.send("100000")
        self.send("cotización")
        self.assertEqual(self.send("¿Cómo puedo vender más?"), flow.PASS_TO_AI)
        self.send("retomar diagnóstico")
        self.send("revisar después")
        self.assertEqual(guide.guide_items(self.repo.data), before)

    def test_personal_withdrawal_larger_than_sales_requires_confirmation(self):
        self.start()
        for text in ("sí", "0", "sí", "1500000", "3500000"):
            response = self.send(text)
        self.assertIn("$3.500.000", response["body"])
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "confirm_owner")
        self.assertNotIn("owner_amount", self.repo.data["diagnostic"]["answers"])
        self.button("no")
        self.send("350000")
        self.assertEqual(self.repo.data["diagnostic"]["answers"]["owner_amount"], 350000)

    def test_c24_is_increment_and_monthly_already_included_is_not_added(self):
        self.to_costs()
        self.estimate("C24")
        self.estimate("C21_apoyo_mensual_basico", included=True)
        self.button("gdone")
        self.button("yes")
        self.button("confirm")
        result = self.repo.revisions[-1]["result"]
        increment = next(i["amount_clp_max"] for i in self.repo.data["items"] if i["item_key"] == "estimate:C24")
        self.assertEqual(result["future_known_operating_expenses"], 200000 + increment)
        self.assertEqual(result["current_expenses"], 200000)

    def test_unknown_payments_never_become_zero_or_capacity_high(self):
        self.to_costs()
        self.button("gcost_C16")
        self.button("gskip")
        self.button("gdone")
        self.button("yes")
        self.button("confirm")
        result = self.repo.revisions[-1]["result"]
        self.assertIsNone(result["economic_assessment"]["capacity"])
        self.assertIn("applicability:estimate:C16", result["economic_assessment"]["missing_fields"])
        self.assertIsNone(result["projection"]["capacity"])

    def test_economic_assessment_is_separate_from_legal_validation(self):
        self.to_costs()
        self.estimate("C12")
        # User explicitly excludes all remaining concepts for this hypothetical scenario.
        for row in self.repo.data["diagnostic"]["answers"]["estimate_decisions"]:
            if row["key"] != "C12":
                self.button("gcost_" + row["key"])
                self.button("gexclude")
        self.button("gdone")
        self.button("yes")
        response = self.button("confirm")
        saved = self.repo.revisions[-1]
        self.assertEqual(saved["assessment_status"], "partial")
        self.assertIsNone(saved["capacity"])
        self.assertIn(saved["result"]["economic_assessment"]["capacity"], {"alta", "media", "baja"})
        self.assertFalse(saved["result"]["economic_assessment"]["legal_validation"])
        self.assertIn("orientativa", response["body"])

    def test_invalid_amount_paid_and_old_button_preserve_progress(self):
        self.to_costs()
        self.button("gcost_C12")
        old = flow._token(self.repo.data["diagnostic"], "gaccept")
        self.send(old)
        before = copy.deepcopy(self.repo.data)
        result = self.send("9000000000000")
        self.assertIn("no puede superar", result["body"])
        self.assertEqual(self.repo.data, before)
        self.assertIn("otra pregunta", self.send(old))
        self.assertEqual(self.repo.data, before)

    def test_dates_and_annual_totals_are_not_spread_over_twelve_months(self):
        self.to_costs()
        self.button("gcost_C21_renta_anual_separada")
        self.button("gaccept")
        self.send("0")
        self.send("15/12/2026")
        self.button("gdone")
        self.button("yes")
        self.button("confirm")
        result = self.repo.revisions[-1]["result"]
        months = result["projection"]["months"]
        self.assertEqual(months[1]["outflows"] - months[0]["outflows"], 200)

    def test_result_and_sources_are_paginated_and_reusable_after_confirmation(self):
        self.to_costs()
        self.estimate("C12")
        self.button("gdone")
        self.button("no")
        self.button("confirm")
        result = self.button("details")
        count = 0
        while any(label == "Siguiente" for _, label in result["options"]):
            action = next(token for token, label in result["options"] if label == "Siguiente")
            result = self.send(action)
            count += 1
            self.assertLess(count, 30)
        self.assertGreater(count, 0)
        self.assertIn("Estimación de FinancIAl", self.button("gsummary")["body"])
        self.assertIn("test-v1", self.button("sources")["body"])

    def test_expired_or_changed_scope_blocks_orientative_classification(self):
        self.to_costs()
        d = self.repo.data["diagnostic"]
        d["answers"].update(budget_as_of="2027-01-01", estimate_coverage_confirmed=True)
        d["comuna_code"] = "el_bosque"
        result = evaluate_diagnostic(self.repo.get())
        self.assertIn("estimate_catalog_expired", result.missing_fields)
        self.assertIn("estimate_profile_changed", result.missing_fields)
        self.assertIsNone(result.result["economic_assessment"]["capacity"])

    def test_real_seed_all_cost_cards_fit_whatsapp_limits(self):
        self.catalog["document"] = real_catalog()
        self.catalog["version"] = self.catalog["document"]["version"]
        self.to_costs()
        for row in self.repo.data["diagnostic"]["answers"]["estimate_decisions"]:
            self.button("gcost_" + row["key"])
            self.button("gscope")
            self.button("gbackcost")
            self.button("gskip")


if __name__ == "__main__":
    unittest.main()
