import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from core.financial_parser import (
    extract_financial_movement,
    extract_with_rules,
    looks_like_financial_movement,
    parse_chilean_amount,
)


class FinancialParserTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 17, 12, 0, tzinfo=ZoneInfo("UTC"))

    def test_parses_common_chilean_amount_formats(self):
        self.assertEqual(parse_chilean_amount("vendí $40.000"), 40000)
        self.assertEqual(parse_chilean_amount("gasté 15 lucas"), 15000)
        self.assertEqual(parse_chilean_amount("recibí 40 mil pesos"), 40000)

    def test_complete_income_is_classified(self):
        result = extract_with_rules(
            "hoy vendí 40.000 pesos en empanadas",
            now=self.now,
        )

        self.assertEqual(result.movement_type, "income")
        self.assertEqual(result.amount, 40000)
        self.assertEqual(result.category, "ventas")
        self.assertIn("empanadas", result.description)
        self.assertEqual(result.missing_fields, [])

    def test_product_quantity_is_not_treated_as_money(self):
        result = extract_with_rules(
            "hoy vendí 10 empanadas",
            now=self.now,
        )

        self.assertIsNone(result.amount)
        self.assertIn("amount", result.missing_fields)
        self.assertIn("10 empanadas", result.description)

    def test_expense_category_is_detected(self):
        result = extract_with_rules(
            "ayer gasté $18.500 en harina y aceite",
            now=self.now,
        )

        self.assertEqual(result.movement_type, "expense")
        self.assertEqual(result.amount, 18500)
        self.assertEqual(result.category, "insumos_mercaderia")
        self.assertEqual(result.occurred_on.isoformat(), "2026-09-16")

    def test_expense_without_concept_requests_missing_description(self):
        with patch(
            "core.financial_parser._extract_with_llm",
            return_value={
                "movement_type": "expense",
                "amount": 15000,
                "category": "arriendo_servicios",
                "description": "gasté 15000",
                "occurred_on": "2026-09-17",
                "confidence": 0.95,
            },
        ):
            result = extract_financial_movement(
                "gasté 15000",
                now=self.now,
            )

        self.assertEqual(result.movement_type, "expense")
        self.assertEqual(result.amount, 15000)
        self.assertIsNone(result.description)
        self.assertIsNone(result.category)
        self.assertIn("description", result.missing_fields)
        self.assertIn("category", result.missing_fields)

    def test_missing_concept_can_be_completed_in_follow_up(self):
        incomplete = extract_with_rules("gasté 15000", now=self.now)
        completed = extract_with_rules(
            "harina y aceite",
            existing_draft=incomplete.draft_payload(),
            now=self.now,
        )

        self.assertEqual(completed.amount, 15000)
        self.assertIn("harina y aceite", completed.description)
        self.assertEqual(completed.category, "insumos_mercaderia")
        self.assertEqual(completed.missing_fields, [])

    def test_question_is_not_registered_as_movement(self):
        self.assertFalse(
            looks_like_financial_movement(
                "¿Cuánto debo vender para ganar 40.000 pesos?"
            )
        )


if __name__ == "__main__":
    unittest.main()
