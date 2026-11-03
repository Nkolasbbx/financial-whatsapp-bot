"""Fase 3 de optimización (P-10): contador de mensajes y checkpoint del resumen."""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import dependencies
import core.ia as ia
from db import users as db_users

USER_ID = "11111111-2222-3333-4444-555555555555"
PHONE = "56911111111"
ASKED_AT = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


class RpcError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def admin_with(data=None, error=None):
    client = MagicMock()
    execute = client.rpc.return_value.execute
    if error is not None:
        execute.side_effect = error
    else:
        execute.return_value = SimpleNamespace(data=data)
    return client


class AppendMessagesTests(unittest.TestCase):
    def test_returns_total_from_scalar_list_or_row(self):
        for data in (12, [12], [{"append_messages_v1": 12}]):
            with patch.object(dependencies, "supabase_admin", admin_with(data)):
                total = db_users.append_messages_by_user_id(USER_ID, PHONE, [{"role": "user", "content": "hola"}])
            self.assertEqual(total, 12, data)

    def test_sends_only_known_fields(self):
        client = admin_with(3)
        with patch.object(dependencies, "supabase_admin", client):
            db_users.append_messages_by_user_id(USER_ID, PHONE, [
                {"role": "user", "content": "hola", "created_at": "2026-11-01T12:00:00+00:00", "extra": "x"},
            ])
        name, params = client.rpc.call_args.args
        self.assertEqual(name, "append_messages_v1")
        self.assertEqual(params["p_messages"], [
            {"role": "user", "content": "hola", "created_at": "2026-11-01T12:00:00+00:00"},
        ])

    def test_missing_function_is_reported_as_unavailable(self):
        for code in ("PGRST202", "42883"):
            with patch.object(dependencies, "supabase_admin", admin_with(error=RpcError(code))):
                with self.assertRaises(db_users.MessageCounterUnavailable):
                    db_users.append_messages_by_user_id(USER_ID, PHONE, [{"role": "user", "content": "x"}])

    def test_ambiguous_errors_propagate_instead_of_falling_back(self):
        with patch.object(dependencies, "supabase_admin", admin_with(error=TimeoutError("lento"))):
            with self.assertRaises(TimeoutError):
                db_users.append_messages_by_user_id(USER_ID, PHONE, [{"role": "user", "content": "x"}])


class PersistTurnTests(unittest.TestCase):
    user = {"id": USER_ID, "phone": PHONE}

    def persist(self, append):
        with (
            patch.object(ia, "OPT_MESSAGE_COUNTER", True),
            patch.object(ia, "append_messages_by_user_id", append),
            patch.object(ia, "save_messages_by_user_id") as save,
            patch.object(ia, "count_messages_by_user_id", return_value=40) as count,
        ):
            total = ia._persist_turn(self.user, PHONE, "pregunta", "respuesta", ASKED_AT)
        return total, save, count

    def test_counter_saves_and_counts_in_one_call(self):
        append = MagicMock(return_value=21)
        total, save, count = self.persist(append)
        self.assertEqual(total, 21)
        save.assert_not_called()
        count.assert_not_called()
        rows = append.call_args.args[2]
        self.assertEqual([row["role"] for row in rows], ["user", "assistant"])
        self.assertLess(rows[0]["created_at"], rows[1]["created_at"])

    def test_missing_rpc_falls_back_to_insert_and_count(self):
        total, save, count = self.persist(MagicMock(side_effect=db_users.MessageCounterUnavailable("PGRST202")))
        self.assertEqual(total, 40)
        save.assert_called_once()
        count.assert_called_once()

    def test_ambiguous_error_does_not_insert_twice(self):
        total, save, _ = self.persist(MagicMock(side_effect=TimeoutError("lento")))
        self.assertEqual(total, 0)
        save.assert_not_called()

    def test_flag_off_keeps_insert_and_count(self):
        with (
            patch.object(ia, "OPT_MESSAGE_COUNTER", False),
            patch.object(ia, "append_messages_by_user_id") as append,
            patch.object(ia, "save_messages_by_user_id") as save,
            patch.object(ia, "count_messages_by_user_id", return_value=5),
        ):
            self.assertEqual(ia._persist_turn(self.user, PHONE, "p", "r", ASKED_AT), 5)
        append.assert_not_called()
        save.assert_called_once()


class SummaryDecisionTests(unittest.TestCase):
    def test_counter_summarizes_after_n_new_messages_since_checkpoint(self):
        with patch.object(ia, "OPT_MESSAGE_COUNTER", True), patch.object(ia, "RESUMEN_FRECUENCIA_MENSAJES", 10):
            self.assertFalse(ia._debe_resumir({"summary_checkpoint": 30}, 39))
            self.assertTrue(ia._debe_resumir({"summary_checkpoint": 30}, 40))
            # Aunque el total se salte el múltiplo exacto, no se pierde el resumen.
            self.assertTrue(ia._debe_resumir({"summary_checkpoint": 30}, 43))

    def test_without_counter_keeps_multiple_crossing_rule(self):
        with patch.object(ia, "OPT_MESSAGE_COUNTER", False), patch.object(ia, "RESUMEN_FRECUENCIA_MENSAJES", 10):
            self.assertTrue(ia._debe_resumir({"summary_checkpoint": 30}, 10))
            self.assertFalse(ia._debe_resumir({"summary_checkpoint": 30}, 13))


class RecordingUsersTable:
    def __init__(self, updated_rows):
        self.updated_rows = updated_rows
        self.payload = None
        self.filters = []

    def update(self, payload):
        self.payload = payload
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def execute(self):
        return SimpleNamespace(data=self.updated_rows)


class SummaryCheckpointTests(unittest.TestCase):
    def run_summary(self, updated_rows):
        table = RecordingUsersTable(updated_rows)
        user = {"resumen_conversacion": "previo", "summary_checkpoint": 30, "message_count": 41}
        with (
            patch.object(ia, "OPT_MESSAGE_COUNTER", True),
            patch.object(ia, "RES_KEY", "k"), patch.object(ia, "RES_MODEL", "m"),
            patch.object(ia, "get_messages", return_value=[{"role": "user", "content": "hola"}]),
            patch.object(ia, "get_user", return_value=user),
            patch.object(ia, "llamar_llm", return_value="Temas principales:\n- Patente"),
            patch.object(dependencies, "supabase_admin", SimpleNamespace(table=lambda name: table)),
        ):
            result = ia.actualizar_resumen_conversacion(PHONE)
        return result, table

    def test_saves_summary_and_advances_checkpoint_only_if_unchanged(self):
        result, table = self.run_summary(updated_rows=[{"phone": PHONE}])
        self.assertEqual(result, "Temas principales:\n- Patente")
        self.assertEqual(table.payload["summary_checkpoint"], 41)
        self.assertIn(("summary_checkpoint", 30), table.filters)

    def test_concurrent_summary_that_lost_the_race_is_discarded(self):
        result, _ = self.run_summary(updated_rows=[])
        self.assertIsNone(result)


class WorkerBudgetWithCounterTests(unittest.IsolatedAsyncioTestCase):
    async def test_ai_worker_uses_three_supabase_calls(self):
        calls = []

        class Table:
            def __init__(self, name):
                self.name = name

            def __getattr__(self, method):
                return lambda *a, **k: self

            def execute(self):
                calls.append(self.name)
                return SimpleNamespace(data=[], count=0)

        admin = MagicMock()
        admin.table.side_effect = lambda name: Table(name)
        admin.rpc.return_value.execute.side_effect = lambda: (calls.append("rpc:append_messages_v1"), SimpleNamespace(data=8))[1]
        user = {"id": USER_ID, "phone": PHONE, "comuna": "", "rubro": "", "summary_checkpoint": 0}

        def get_user(phone):
            calls.append("users")
            return user

        with (
            patch.object(dependencies, "supabase_admin", admin),
            patch.object(ia, "OPT_MESSAGE_COUNTER", True),
            patch.object(ia, "get_user", side_effect=get_user),
            patch.object(ia, "requiere_rag", AsyncMock(return_value=(False, None))),
            patch.object(ia, "llamar_llm_async", AsyncMock(return_value="Respuesta")),
            patch.object(ia, "_build_personalized_context", return_value=""),
            patch.object(ia, "_send_ai_response_with_menu", AsyncMock()),
            patch.object(ia, "actualizar_resumen_en_background", MagicMock()),
        ):
            await ia.process_ai_and_send(PHONE, "¿Qué hago?", True)

        self.assertEqual(calls, ["users", "messages", "rpc:append_messages_v1"])


if __name__ == "__main__":
    unittest.main()
