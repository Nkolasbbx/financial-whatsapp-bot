import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from db.fondos import (
    find_active_fund,
    get_active_fund_session,
    get_fund_answers,
    normalize_fund_text,
    save_fund_answer,
    start_fund_session,
    update_fund_session,
)


class FundDatabaseTests(unittest.TestCase):
    def test_normalize_fund_text_ignores_accents_and_separators(self):
        self.assertEqual(
            normalize_fund_text("  Capital_Pionéras---Emprende "),
            "capital pioneras emprende",
        )

    @patch("db.fondos.list_active_funds")
    def test_find_active_fund_accepts_alias(self, list_funds_mock):
        expected = {
            "id": "fund-1",
            "slug": "capital_pioneras_emprende",
            "nombre": "Capital Pioneras Emprende",
            "aliases": ["capital pioneras", "fondo pioneras"],
        }
        list_funds_mock.return_value = [expected]

        self.assertEqual(find_active_fund("Fondo Pionéras"), expected)

    @patch("db.fondos.list_active_funds", return_value=[])
    def test_find_active_fund_returns_none_when_not_found(self, _list_funds_mock):
        self.assertIsNone(find_active_fund("fondo inexistente"))

    def test_start_session_uses_user_conflict_and_collecting_status(self):
        client = MagicMock()
        query = client.table.return_value
        query.upsert.return_value.execute.return_value = SimpleNamespace(
            data=[{"user_id": "user-1", "fondo_id": "fund-1"}]
        )
        fake_dependencies = SimpleNamespace(supabase_admin=client)

        with patch.dict(sys.modules, {"dependencies": fake_dependencies}):
            result = start_fund_session("user-1", "fund-1")

        self.assertEqual(result["fondo_id"], "fund-1")
        payload = query.upsert.call_args.args[0]
        self.assertEqual(payload["status"], "collecting_data")
        self.assertIsNone(payload["pending_field_key"])
        self.assertEqual(
            query.upsert.call_args.kwargs["on_conflict"],
            "user_id",
        )

    def test_get_active_session_ignores_finished_session(self):
        client = MagicMock()
        query = client.table.return_value
        query.select.return_value.eq.return_value.limit.return_value.execute.return_value = (
            SimpleNamespace(data=[{"status": "evaluated"}])
        )
        fake_dependencies = SimpleNamespace(supabase_admin=client)

        with patch.dict(sys.modules, {"dependencies": fake_dependencies}):
            result = get_active_fund_session("user-1")

        self.assertIsNone(result)

    def test_save_unknown_answer_uses_json_marker(self):
        client = MagicMock()
        query = client.table.return_value
        query.upsert.return_value.execute.return_value = SimpleNamespace(
            data=[{"field_key": "mayor_edad"}]
        )
        fake_dependencies = SimpleNamespace(supabase_admin=client)

        with patch.dict(sys.modules, {"dependencies": fake_dependencies}):
            save_fund_answer("user-1", "mayor_edad", None)

        payload = query.upsert.call_args.args[0]
        self.assertEqual(payload["value"], {"status": "unknown"})
        self.assertEqual(
            query.upsert.call_args.kwargs["on_conflict"],
            "user_id,field_key",
        )

    def test_get_answers_decodes_unknown_marker(self):
        client = MagicMock()
        query = client.table.return_value
        query.select.return_value.eq.return_value.execute.return_value = SimpleNamespace(
            data=[
                {"field_key": "mayor_edad", "value": True},
                {
                    "field_key": "proyecto_negocio",
                    "value": {"status": "unknown"},
                },
            ]
        )
        fake_dependencies = SimpleNamespace(supabase_admin=client)

        with patch.dict(sys.modules, {"dependencies": fake_dependencies}):
            result = get_fund_answers("user-1")

        self.assertEqual(
            result,
            {"mayor_edad": True, "proyecto_negocio": None},
        )

    def test_update_session_rejects_invalid_status(self):
        with self.assertRaisesRegex(ValueError, "Estado de sesión"):
            update_fund_session("user-1", status="invalid")


if __name__ == "__main__":
    unittest.main()
