"""Reglas HDU017 con dinero ficticio. No consultan fuentes ni Supabase."""

import copy
import unittest
from datetime import date
from decimal import Decimal

from core.formalization_engine import (
    CashEvent, RULES_CONFIG, RULES_VERSION, catalog_applicability, convert_clp,
    evaluate_diagnostic, evaluate_projection, month_after,
)


START = date(2026, 11, 1)


def scenario(cash=1500000, initial=500000, income=1200000, payments=1000000):
    events = [CashEvent("initial", date(2026, 10, 25), initial, "outflow")]
    for index in range(3):
        month = month_after(START, index)
        events += [CashEvent(f"sale{index}", month, income, "inflow", business_income=True),
                   CashEvent(f"pay{index}", month.replace(day=28), payments, "outflow", recurring=True)]
    return evaluate_projection(cash, START, events)


def item(key, amount, **changes):
    return dict({"id": key, "item_key": key, "label": key, "item_kind": "current_operating",
                 "economic_nature": "expense", "cash_direction": "outflow", "applicability": "applies",
                 "amount_clp_min": amount, "amount_clp_max": amount, "amount_paid_clp": 0,
                 "amount_state": "confirmed", "frequency": "monthly", "source_type": "user",
                 "schedule": [], "evidence": {}, "metadata": {}}, **changes)


def context():
    return {"diagnostic": {"activity_code": "alimentos", "comuna_code": "recoleta", "location_type": "local",
                           "period_start": "2026-09-01", "period_end": "2026-10-01", "projection_start": str(START),
                           "financial_snapshot": {"loaded": True}, "rules_version": RULES_VERSION,
                           "rules_config": dict(RULES_CONFIG), "answers": {
                               **dict.fromkeys(("expenses_confirmed", "income_confirmed", "representative",
                                                "calendar_confirmed", "preparation_confirmed", "owner_confirmed",
                                                "debt_confirmed", "catalog_coverage_confirmed"), True),
                               "available_cash": 5000000}},
            "items": [item("rent", 200000), item("sales", 1000000, cash_direction="inflow", economic_nature="revenue")]}


class ProjectionRulesTests(unittest.TestCase):
    def test_rf01_to_rf07_boundaries(self):
        cases = [({}, "alta"), ({"cash": 1499999}, "media"), ({"cash": 499999}, "baja"),
                 ({"cash": 1000000, "initial": 100000, "income": 1000000, "payments": 900000}, "media"),
                 ({"cash": 950000, "initial": 100000, "income": 1000000, "payments": 850000}, "alta"),
                 ({"cash": 950000, "initial": 100000, "income": 1000000, "payments": 850001}, "media"),
                 ({"cash": 5000000, "initial": 0, "income": 1000000, "payments": 1050000}, "baja")]
        for values, expected in cases:
            with self.subTest(values=values):
                self.assertEqual(scenario(**values)["capacity"], expected)

    def test_rf09_no_cash_is_medium_only_with_feasible_dates(self):
        self.assertEqual(scenario(cash=0, initial=0, income=1000000, payments=800000)["capacity"], "media")

    def test_rf10_no_income_is_low_without_division_by_zero(self):
        self.assertEqual(scenario(cash=1000000, initial=0, income=0, payments=100000)["capacity"], "baja")

    def test_rf11_zero_activity_not_evaluable(self):
        result = scenario(cash=100000, initial=0, income=0, payments=0)
        self.assertIsNone(result["capacity"])

    def test_rf12_payments_before_sales_reveal_cash_gap(self):
        result = evaluate_projection(500000, START, [CashEvent("pay", START, 1000000, "outflow", recurring=True),
            CashEvent("sale", date(2026, 11, 28), 1200000, "inflow", business_income=True)])
        self.assertEqual(result["capacity"], "baja")
        self.assertEqual(result["first_uncovered_payment"]["shortfall"], 500000)

    def test_rf14_preparation_income_cannot_pay_earlier_bill(self):
        result = evaluate_projection(500000, START, [
            CashEvent("pay", date(2026, 10, 21), 600000, "outflow"),
            CashEvent("funding", date(2026, 10, 29), 200000, "inflow"),
            CashEvent("sale", START, 1000000, "inflow", business_income=True)])
        self.assertEqual(result["opening_balance"], 100000)
        self.assertEqual(result["capacity"], "baja")

    def test_same_day_pays_before_collecting_and_financing_is_not_stressed(self):
        result = evaluate_projection(0, START, [CashEvent("fund", START, 100, "inflow"),
            CashEvent("bill", START, 100, "outflow", recurring=True)])
        self.assertEqual(result["minimum_balance"], -100)
        self.assertEqual(result["stress_minimum_balance"], -100)
        self.assertEqual(result["months"][0]["stress_inflows"], 100)

    def test_reserve_not_subtracted_again(self):
        result = scenario()
        self.assertEqual(result["opening_balance"], 1000000)
        self.assertEqual(result["months"][0]["closing_balance"], 1200000)

    def test_conversion_and_stress_round_prudently(self):
        self.assertEqual(convert_clp(Decimal("1.01"), Decimal("10")), 11)
        self.assertEqual(convert_clp(Decimal("1.01"), Decimal("10"), inflow=True), 10)
        result = evaluate_projection(0, START, [CashEvent("sale", START, 101, "inflow", business_income=True)])
        self.assertEqual(result["months"][0]["stress_inflows"], 85)
        with self.assertRaises(ValueError):
            convert_clp(Decimal("NaN"), Decimal("1"))

    def test_duplicate_events_and_negative_amounts_rejected(self):
        event = CashEvent("a", START, 1, "inflow")
        with self.assertRaises(ValueError):
            evaluate_projection(0, START, [event, event])
        with self.assertRaises(ValueError):
            CashEvent("bad", START, True, "inflow")


class DiagnosticEngineTests(unittest.TestCase):
    def test_catalog_conditions_preserve_unknown_and_exclude_only_known_false(self):
        concept = {"conditions": {"version": 1, "all": [
            {"field": "inicio_sii", "operator": "equals", "value": "no"},
            {"field": "required", "operator": "equals", "value": True}], "requires": ["quote"]}}
        self.assertEqual(catalog_applicability(concept, {"inicio_sii": "si"}), "not_applicable")
        self.assertEqual(catalog_applicability(concept, {"inicio_sii": "no"}), "to_confirm")
        self.assertEqual(catalog_applicability(concept, {"inicio_sii": "no", "required": True, "quote": 0}), "applies")

    def test_known_projection_complete(self):
        result = evaluate_diagnostic(context())
        self.assertEqual(result.assessment_status, "complete")
        self.assertEqual(result.capacity, "alta")

    def test_rf08_unknown_tax_partial_never_zero_or_low(self):
        data = context()
        data["items"].append(item("tax", None, item_kind="future_operating", economic_nature="tax", amount_state="pending"))
        result = evaluate_diagnostic(data)
        self.assertEqual(result.assessment_status, "partial")
        self.assertIsNone(result.capacity)
        self.assertIsNone(result.result["projection"]["capacity"])
        self.assertIn("amount:tax", result.missing_fields)

    def test_rf13_ranges_and_prior_payments(self):
        data = context()
        data["items"].append(item("equipment", 400000, amount_clp_max=500000, amount_paid_clp=100000,
                                  item_kind="initial_payment", economic_nature="investment", frequency="once", due_date="2026-10-20"))
        result = evaluate_diagnostic(data)
        self.assertEqual(result.result["initial_remaining_min"], 300000)
        self.assertEqual(result.result["initial_remaining_max"], 400000)
        self.assertEqual(result.result["projection"]["preparation_outflows"], 400000)

    def test_replacement_and_included_cost_not_added_twice(self):
        data = context()
        data["items"] += [item("newrent", 250000, item_kind="future_operating", replaces_item_id="rent"),
                           item("accounting", 100000, item_kind="future_operating", included_in_baseline=True)]
        result = evaluate_diagnostic(data)
        self.assertEqual(result.result["current_expenses"], 200000)
        self.assertEqual(result.result["future_known_operating_expenses"], 250000)
        self.assertEqual(result.result["projection"]["months"][0]["outflows"], 250000)

    def test_projected_sales_replace_current_without_changing_baseline(self):
        data = context()
        data["items"].append(item("newincome", 1200000, item_kind="future_operating", cash_direction="inflow",
                                  economic_nature="revenue", replaces_item_id="sales"))
        result = evaluate_diagnostic(data)
        self.assertEqual(result.result["current_income"], 1000000)
        self.assertEqual(result.result["future_known_income"], 1200000)
        self.assertEqual(result.result["future_known_operating_result"], 1000000)

    def test_financing_and_withdrawals_are_cash_not_operating_sales_or_expense(self):
        data = context()
        data["items"] += [item("fund", 50000, item_kind="financing", economic_nature="capital_contribution", cash_direction="inflow",
                              metadata={"commitment_confirmed": True}),
                           item("owner", 10000, item_kind="owner_withdrawal", economic_nature="owner_withdrawal")]
        result = evaluate_diagnostic(data)
        self.assertEqual(result.result["current_income"], 1000000)
        self.assertEqual(result.result["future_known_operating_result"], 800000)
        self.assertEqual(result.result["projection"]["months"][0]["inflows"], 1050000)

    def test_pending_source_blocks_even_user_says_amount_is_confirmed(self):
        data = context()
        data["items"][0]["catalog_snapshot"] = {"pricing_method": "formula", "review_status": "pending"}
        self.assertIn("source_review:rent", evaluate_diagnostic(data).missing_fields)

    def test_no_coverage_or_cash_never_classifies(self):
        for key in ("income_confirmed", "catalog_coverage_confirmed", "available_cash"):
            data = context()
            data["diagnostic"]["answers"].pop(key)
            result = evaluate_diagnostic(data)
            self.assertIsNone(result.capacity)
            self.assertIn(key, result.missing_fields)

    def test_nonmonthly_cost_needs_real_date_not_monthly_division(self):
        data = context()
        data["items"].append(item("annual", 120000, frequency="annual", item_kind="future_operating"))
        self.assertIn("schedule:annual", evaluate_diagnostic(data).missing_fields)
        data["items"][-1]["due_date"] = "2026-12-15"
        result = evaluate_diagnostic(data)
        self.assertEqual([m["outflows"] for m in result.result["projection"]["months"]], [200000, 320000, 200000])

    def test_annual_payment_deducts_prior_deposit_once(self):
        data = context()
        data["items"].append(item("permit", 120000, amount_paid_clp=20000, frequency="annual",
                                  item_kind="future_operating", due_date="2026-12-15"))
        self.assertEqual(evaluate_diagnostic(data).result["projection"]["months"][1]["outflows"], 300000)

    def test_uncommitted_funding_cannot_improve_projection(self):
        data = context()
        data["items"].append(item("fund", 999999, item_kind="financing", economic_nature="capital_contribution", cash_direction="inflow"))
        result = evaluate_diagnostic(data)
        self.assertIsNone(result.capacity)
        self.assertEqual(result.result["projection"]["months"][0]["inflows"], 1000000)
        self.assertIn("financing_commitment:fund", result.missing_fields)

    def test_incomplete_schedule_cannot_hide_monthly_obligations(self):
        data = context()
        data["items"][0]["schedule"] = [{"on": "2026-11-01", "amount_clp": 100}]
        self.assertIn("schedule_total:rent", evaluate_diagnostic(data).missing_fields)

    def test_unchanged_input_deterministic_and_not_mutated(self):
        data = context()
        original = copy.deepcopy(data)
        self.assertEqual(evaluate_diagnostic(data), evaluate_diagnostic(data))
        self.assertEqual(data, original)


if __name__ == "__main__":
    unittest.main()
