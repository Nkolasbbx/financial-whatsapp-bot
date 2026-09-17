import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from db import financial_movements


class FinancialMovementsDatabaseTests(unittest.TestCase):
    @patch.object(financial_movements, "_admin_client")
    def test_confirm_uses_atomic_rpc(self, admin_client_mock):
        client = MagicMock()
        client.rpc.return_value.execute.return_value = SimpleNamespace(
            data={
                "id": "movement-1",
                "movement_type": "income",
                "amount": 40000,
            }
        )
        admin_client_mock.return_value = client

        result = financial_movements.confirm_financial_movement(
            "user-1",
            "income",
            40000,
            "ventas",
            "Venta de empanadas",
            date(2026, 9, 17),
            original_text="vendí 40.000 en empanadas",
        )

        self.assertEqual(result["id"], "movement-1")
        client.rpc.assert_called_once_with(
            "confirm_financial_movement",
            {
                "p_user_id": "user-1",
                "p_movement_type": "income",
                "p_amount": 40000,
                "p_category": "ventas",
                "p_description": "Venta de empanadas",
                "p_occurred_on": "2026-09-17",
                "p_original_text": "vendí 40.000 en empanadas",
                "p_target_movement_id": None,
            },
        )

    @patch.object(financial_movements, "_admin_client")
    def test_month_summary_uses_exclusive_end_date(self, admin_client_mock):
        client = MagicMock()
        client.rpc.return_value.execute.return_value = SimpleNamespace(
            data={
                "month_start": "2026-09-01",
                "month_end": "2026-10-01",
                "income_total": 100000,
                "expense_total": 25000,
                "net_total": 75000,
                "movement_count": 2,
                "income_categories": [],
                "expense_categories": [],
            }
        )
        admin_client_mock.return_value = client

        result = financial_movements.get_financial_month_summary(
            "user-1",
            date(2026, 9, 1),
            date(2026, 10, 1),
        )

        self.assertEqual(result["net_total"], 75000)
        client.rpc.assert_called_once_with(
            "get_financial_month_summary",
            {
                "p_user_id": "user-1",
                "p_month_start": "2026-09-01",
                "p_month_end": "2026-10-01",
            },
        )

    def test_rejects_category_from_wrong_movement_type(self):
        with self.assertRaises(ValueError):
            financial_movements.confirm_financial_movement(
                "user-1",
                "income",
                40000,
                "transporte",
                "Venta",
                date(2026, 9, 17),
            )


if __name__ == "__main__":
    unittest.main()

