import unittest
from unittest.mock import patch

from core import financial_flow


class FinancialFlowTests(unittest.TestCase):
    def setUp(self):
        self.user = {
            "id": "user-1",
            "phone": "+56911111111",
            "rubro": "alimentos",
            "onboarding_step": "done",
        }

    @patch.object(financial_flow, "start_financial_session")
    def test_new_income_starts_persistent_draft(self, start_mock):
        result = financial_flow.handle_financial_message(
            self.user,
            financial_flow.FINANCIAL_NEW_INCOME_ID,
        )

        start_mock.assert_called_once()
        self.assertEqual(start_mock.call_args.args[:2], ("user-1", "waiting_missing_data"))
        self.assertEqual(
            start_mock.call_args.kwargs["draft"],
            {"movement_type": "income"},
        )
        self.assertIn("ingreso", result["body"])

    def test_natural_movement_is_sent_to_worker(self):
        result = financial_flow.handle_financial_message(
            self.user,
            "hoy vendí $40.000 en empanadas",
        )

        self.assertEqual(result, financial_flow.FINANCIAL_PARSE_TASK)

    @patch.object(financial_flow, "get_financial_month_summary")
    def test_month_summary_shows_totals_and_categories(self, summary_mock):
        summary_mock.return_value = {
            "month_start": "2026-09-01",
            "month_end": "2026-10-01",
            "income_total": 100000,
            "expense_total": 35000,
            "net_total": 65000,
            "movement_count": 3,
            "income_categories": [{"category": "ventas", "total": 100000}],
            "expense_categories": [
                {"category": "insumos_mercaderia", "total": 35000}
            ],
        }

        result = financial_flow.handle_financial_message(
            self.user,
            financial_flow.FINANCIAL_SUMMARY_ID,
        )

        self.assertIn("$100.000", result["body"])
        self.assertIn("$35.000", result["body"])
        self.assertIn("$65.000", result["body"])
        self.assertIn("Insumos y mercadería", result["body"])

    @patch.object(financial_flow, "confirm_financial_movement")
    def test_confirmation_persists_validated_draft(self, confirm_mock):
        confirm_mock.return_value = {
            "id": "movement-1",
            "user_id": "user-1",
            "movement_type": "income",
            "amount": 40000,
            "currency": "CLP",
            "category": "ventas",
            "description": "Venta de empanadas",
            "occurred_on": "2026-09-17",
            "status": "confirmed",
        }
        session = {
            "state": "confirming_creation",
            "draft": {
                "movement_type": "income",
                "amount": 40000,
                "category": "ventas",
                "description": "Venta de empanadas",
                "occurred_on": "2026-09-17",
                "original_text": "vendí 40.000 en empanadas",
            },
            "target_movement_id": None,
        }

        result = financial_flow.handle_financial_message(
            self.user,
            financial_flow.FINANCIAL_CONFIRM_ID,
            session,
        )

        confirm_mock.assert_called_once()
        self.assertIn("Movimiento registrado", result["body"])

    @patch.object(financial_flow, "get_financial_month_summary")
    def test_empty_summary_uses_examples_for_food_business(self, summary_mock):
        summary_mock.return_value = {
            "month_start": "2026-09-01",
            "month_end": "2026-10-01",
            "income_total": 0,
            "expense_total": 0,
            "net_total": 0,
            "movement_count": 0,
            "income_categories": [],
            "expense_categories": [],
        }

        result = financial_flow.handle_financial_message(
            self.user,
            "resumen del mes",
        )

        self.assertIn("empanadas", result["body"])
        self.assertIn("harina", result["body"])

    def test_other_module_button_exits_financial_session(self):
        self.assertTrue(
            financial_flow.should_exit_financial_message("calendar_view")
        )
        self.assertFalse(
            financial_flow.should_exit_financial_message(
                financial_flow.FINANCIAL_CONFIRM_ID
            )
        )


if __name__ == "__main__":
    unittest.main()

