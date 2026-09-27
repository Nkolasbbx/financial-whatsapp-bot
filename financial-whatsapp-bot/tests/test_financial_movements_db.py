import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from postgrest.exceptions import APIError

from db import financial_movements


class FinancialMovementsDatabaseTests(unittest.TestCase):
    @patch.object(financial_movements, "_admin_client")
    def test_portal_insert_preserves_whatsapp_draft_and_scopes_request_to_user(self, admin_client_mock):
        client = admin_client_mock.return_value
        client.table.return_value.insert.return_value.execute.return_value = SimpleNamespace(data=[{"id": "saved"}])
        args = ("request-1", "income", 40000, "ventas", " Venta ", date(2026, 9, 27))
        result = financial_movements.create_portal_financial_movement("user-1", *args)
        first_payload = client.table.return_value.insert.call_args.args[0]
        self.assertEqual(result["id"], "saved")
        self.assertEqual(first_payload["description"], "Venta")
        self.assertEqual(first_payload["user_id"], "user-1")
        self.assertEqual(first_payload["occurred_on"], "2026-09-27")
        client.table.assert_called_once_with("financial_movements")
        client.rpc.assert_not_called()
        financial_movements.create_portal_financial_movement("user-2", *args)
        second_payload = client.table.return_value.insert.call_args.args[0]
        self.assertNotEqual(first_payload["id"], second_payload["id"])

    @patch.object(financial_movements, "get_financial_movement")
    @patch.object(financial_movements, "_admin_client")
    def test_portal_retry_recovers_same_record_without_updating_it(self, admin_client_mock, get_mock):
        client = admin_client_mock.return_value
        insert = client.table.return_value.insert
        def duplicate():
            get_mock.return_value = dict(insert.call_args.args[0])
            raise APIError({"code": "23505", "message": "duplicate", "details": "", "hint": ""})
        insert.return_value.execute.side_effect = duplicate
        result = financial_movements.create_portal_financial_movement(
            "user-1", "request-1", "expense", 12000, "transporte", "Traslado", date(2026, 9, 27),
        )
        get_mock.assert_called_once_with("user-1", result["id"], include_deleted=True)
        client.table.return_value.update.assert_not_called()
        client.table.return_value.upsert.assert_not_called()

    @patch.object(financial_movements, "get_financial_movement", return_value={"amount": 1})
    @patch.object(financial_movements, "_admin_client")
    def test_portal_rejects_reused_request_with_changed_data(self, admin_client_mock, get_mock):
        admin_client_mock.return_value.table.return_value.insert.return_value.execute.side_effect = APIError(
            {"code": "23505", "message": "duplicate", "details": "", "hint": ""}
        )
        with self.assertRaises(ValueError):
            financial_movements.create_portal_financial_movement(
                "user-1", "request-1", "income", 40000, "ventas", "Venta", date(2026, 9, 27),
            )

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
