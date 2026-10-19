"""Escenarios de WhatsApp con persistencia simulada, sin tocar Supabase ni la IA."""

import copy
import unittest

from core import formalization_flow as flow
from core.formalization_financial_review import expense_breakdown, snapshot_amount
from tests import test_formalization_flow as helpers

USER = helpers.USER


class FinancialReviewTests(unittest.TestCase):
    setUp = helpers.FlowTests.setUp
    send = helpers.FlowTests.send
    button = helpers.FlowTests.button
    start = helpers.FlowTests.start

    def income(self):
        self.start()
        self.button("yes")
        self.send("0")

    def test_added_expense_is_confirmed_once_and_does_not_change_source(self):
        self.start()
        source = copy.deepcopy(self.repo.data["diagnostic"]["financial_snapshot"])
        refreshes = self.repo.refreshes
        self.button("expense_add")
        response = self.send("$15.000")
        self.assertIn("$215.000", response["body"])
        self.assertNotIn("expenses_confirmed", self.repo.data["diagnostic"]["answers"])
        self.button("yes")
        self.assertEqual(self.repo.data["diagnostic"]["answers"]["expenses_total"], 215000)
        self.assertEqual(self.repo.data["diagnostic"]["financial_snapshot"], source)
        self.assertEqual(self.repo.refreshes, refreshes)

    def test_category_replacement_retains_other_categories_and_supports_resume(self):
        self.start()
        self.button("expense_categories")
        self.button("expense_cat_arriendo_servicios")
        self.send("400000")
        self.send("insumos y mercadería")
        self.assertEqual(self.send("¿Cómo hago un inicio de actividades?"), flow.PASS_TO_AI)
        self.assertEqual(self.repo.data["diagnostic"]["conversation_status"], "paused")
        response = self.send("retomar diagnóstico")
        self.assertIn("$200.000", response["body"])
        self.send("100000")
        self.send("terminar gastos")
        self.send("completo")
        answers = self.repo.data["diagnostic"]["answers"]
        self.assertEqual(answers["expenses_total"], 500000)
        self.assertEqual(answers["expenses_breakdown"]["arriendo_servicios"], 400000)

    def test_manual_total_discards_previous_allocation_without_adding_it(self):
        self.start()
        self.send("800000")
        self.button("expense_categories")
        self.button("expense_cat_sin_desglose")
        self.send("700000")
        self.button("expense_done")
        self.button("yes")
        self.assertEqual(self.repo.data["diagnostic"]["answers"]["expenses_total"], 700000)
        self.assertEqual(self.repo.data["diagnostic"]["answers"]["expenses_breakdown"], {"sin_desglose": 700000})

    def test_zero_history_requires_user_confirmation_and_allows_category_entry(self):
        self.start()
        d = self.repo.data["diagnostic"]
        d["financial_snapshot"].update(expense_total=0, categories=[], operating_income_total=0)
        result = self.send("mi diagnóstico")
        self.assertIn("no significa", result["body"])
        self.assertNotIn("expenses_confirmed", d["answers"])
        self.button("expense_categories")
        self.button("expense_cat_transporte")
        self.send("9000")
        self.button("expense_done")
        self.button("yes")
        self.send("ninguno")
        self.button("income_add")
        self.send("100000")
        self.button("yes")
        self.assertEqual(d["answers"]["income_total"], 100000)
        self.assertEqual(d["answers"]["expenses_total"], 9000)

    def test_oneoff_kept_in_observed_month_but_excluded_from_future_months(self):
        self.start()
        self.button("yes")
        self.send("50000")
        for text in ("sí", "1500000", "0", "0", "sí", "2026-11", "0", "sí"):
            self.send(text)
        helpers.finish_needs(self)
        self.button("gdone")
        self.button("yes")
        self.button("confirm")
        result = self.repo.revisions[0]["result"]
        self.assertEqual(result["current_expenses"], 200000)
        self.assertEqual(result["future_known_operating_expenses"], 150000)
        self.assertEqual(result["current_operating_result"], 300000)
        self.assertEqual(result["future_known_operating_result"], 350000)
        self.assertEqual(result["initial_remaining_max"], 0)
        self.assertEqual(self.repo.refreshes, 2)

    def test_exceptional_expense_cannot_exceed_month_total(self):
        self.start()
        self.button("yes")
        before = copy.deepcopy(self.repo.data)
        result = self.send("200001")
        self.assertIn("no puede superar", result["body"])
        self.assertEqual(self.repo.data, before)

    def test_unknown_oneoff_requires_explicit_forecast_not_assumed_zero(self):
        self.start()
        self.button("yes")
        self.send("no sé")
        for text in ("sí", "1000000", "0", "0"):
            self.send(text)
        response = self.button("yes")
        self.assertIn("esperas vender", response["body"])
        self.send("600000")
        self.send("190000")
        a = self.repo.data["diagnostic"]["answers"]
        self.assertNotIn("expense_oneoff", a)
        self.assertEqual(a["projected_expenses"], 190000)
        self.assertEqual(a["projected_income"], 600000)

    def test_income_add_and_replace_exclude_contributions_and_require_confirmation(self):
        self.income()
        d = self.repo.data["diagnostic"]
        source = copy.deepcopy(d["financial_snapshot"])
        self.button("income_add")
        result = self.send("50000")
        self.assertIn("$550.000", result["body"])
        self.assertNotIn("income_confirmed", d["answers"])
        self.button("income_details")
        self.send("700000")
        self.button("yes")
        self.assertEqual(d["answers"]["income_total"], 700000)
        self.assertEqual(d["financial_snapshot"], source)
        self.assertEqual(d["current_step"], "cash")

    def test_invalid_answer_keeps_category_and_progress(self):
        self.start()
        self.button("expense_categories")
        self.button("expense_cat_marketing")
        before = copy.deepcopy(self.repo.data)
        for text in ("-1", "1.2.3"):
            result = self.send(text)
            self.assertIn("DIAG_VALIDATION", result["body"])
            self.assertEqual(self.repo.data, before)

    def test_edit_expenses_reconfirms_projection_without_overwriting_other_data(self):
        self.income()
        for text in ("sí", "1000000", "0", "0", "sí", "2026-11", "0", "sí"):
            self.send(text)
        helpers.finish_needs(self)
        self.button("gdone")
        self.button("yes")
        self.button("confirm")
        self.send("rehacer diagnóstico")
        prompt = self.button("edit_expenses")
        self.assertIn("Insumos y mercadería: $200.000", prompt["body"])
        self.send("300000")
        self.button("yes")
        self.send("100000")
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "representative")
        self.button("yes")
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "review")
        a = self.repo.data["diagnostic"]["answers"]
        self.assertEqual(a["projected_expenses"], 200000)
        self.assertEqual(a["income_total"], 500000)
        self.assertEqual(a["available_cash"], 1000000)

    def test_period_and_refresh_clear_guided_review_not_reuse_old_adjustments(self):
        for refresh in (False, True):
            with self.subTest(refresh=refresh):
                self.repo.data = {"diagnostic": None, "items": []}
                self.start()
                self.send("300000")
                self.button("yes")
                self.send("100000")
                d = self.repo.data["diagnostic"]
                if refresh:
                    self.button("edit")
                    self.button("edit_refresh")
                else:
                    self.repo.save(USER["id"], d["id"], d["lock_version"], {"current_step": "period"})
                    self.send("2026-08")
                for key in ("expense_review", "expense_oneoff", "expenses_confirmed", "income_review"):
                    self.assertNotIn(key, d["answers"])
                self.assertEqual(d["current_step"], "expenses")

    def test_old_button_cannot_add_missing_amount_twice(self):
        self.income()
        old = flow._token(self.repo.data["diagnostic"], "income_add")
        self.send(old)
        self.send("15000")
        before = copy.deepcopy(self.repo.data)
        self.assertIn("otra pregunta", self.send(old))
        self.assertEqual(self.repo.data, before)


class SnapshotTests(unittest.TestCase):
    def test_null_negative_fractional_or_invalid_values_not_silently_zeroed(self):
        for value in (None, True, "NaN", "Infinity", -1, "1.5", "invalid"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                snapshot_amount(value)
        self.assertEqual(snapshot_amount("15000.00"), 15000)

    def test_missing_category_detail_retains_total_but_inconsistent_sum_fails(self):
        source = {"loaded": True, "expense_total": 10000, "categories": []}
        self.assertEqual(expense_breakdown(source), {"sin_desglose": 10000})
        source["categories"] = [{"movement_type": "expense", "category": "transporte", "total": 20000}]
        with self.assertRaises(ValueError):
            expense_breakdown(source)


if __name__ == "__main__":
    unittest.main()
