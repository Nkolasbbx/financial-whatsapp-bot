import unittest
from datetime import date
from unittest.mock import patch

from schemas.financial_movements import FinancialMovementExtraction
from services import financial_movements


class FinancialMovementServiceTests(unittest.IsolatedAsyncioTestCase):
    @patch.object(financial_movements, "start_financial_session")
    @patch.object(financial_movements, "extract_financial_movement")
    @patch.object(financial_movements, "get_financial_session")
    @patch.object(financial_movements, "get_user")
    async def test_complete_movement_only_creates_confirmable_session(
        self,
        get_user_mock,
        get_session_mock,
        extract_mock,
        start_session_mock,
    ):
        get_user_mock.return_value = {"id": "user-1"}
        get_session_mock.return_value = None
        extract_mock.return_value = FinancialMovementExtraction(
            movement_type="income",
            amount=40000,
            category="ventas",
            description="Venta de empanadas",
            occurred_on=date(2026, 9, 17),
            original_text="vendí 40.000 en empanadas",
            confidence=1,
        )

        result = await financial_movements.prepare_financial_movement(
            "+56911111111",
            "vendí 40.000 en empanadas",
        )

        self.assertEqual(result["type"], "buttons")
        self.assertEqual(result["options"][0][0], "finance_confirm")
        start_session_mock.assert_called_once()
        self.assertEqual(
            start_session_mock.call_args.args[:2],
            ("user-1", "confirming_creation"),
        )

    @patch.object(financial_movements, "start_financial_session")
    @patch.object(financial_movements, "extract_financial_movement")
    @patch.object(financial_movements, "get_financial_session")
    @patch.object(financial_movements, "get_user")
    async def test_incomplete_movement_asks_for_amount(
        self,
        get_user_mock,
        get_session_mock,
        extract_mock,
        start_session_mock,
    ):
        get_user_mock.return_value = {"id": "user-1"}
        get_session_mock.return_value = None
        extract_mock.return_value = FinancialMovementExtraction(
            movement_type="income",
            category="ventas",
            description="Venta de 10 empanadas",
            occurred_on=date(2026, 9, 17),
            original_text="vendí 10 empanadas",
            confidence=0.9,
        )

        result = await financial_movements.prepare_financial_movement(
            "+56911111111",
            "vendí 10 empanadas",
        )

        self.assertIn("monto total", result["body"])
        self.assertEqual(
            start_session_mock.call_args.args[:2],
            ("user-1", "waiting_missing_data"),
        )


if __name__ == "__main__":
    unittest.main()

