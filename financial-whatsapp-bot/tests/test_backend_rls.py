"""Offline regression: private tables must never fall back to the public client."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from db import users
from db.client import get_backend_client
from core.alertas_tributarias import get_alertas_fondos_proximos


class BackendRlsTests(unittest.TestCase):
    def setUp(self):
        self.public = MagicMock()
        self.public.table.side_effect = AssertionError("Public access to private tables")
        self.public.rpc.side_effect = AssertionError("Public RPC access")
        self.admin = MagicMock()
        self.query = self.admin.table.return_value
        for method in ("select", "eq", "limit", "order", "update", "upsert", "insert", "gte", "lte"):
            getattr(self.query, method).return_value = self.query
        self.phone = "+56900000000"
        self.user = {"id": "00000000-0000-0000-0000-000000000001",
                     "phone": self.phone, "role": "user", "content": "test",
                     "bloqueado": False}
        self.query.execute.return_value = SimpleNamespace(data=[self.user], count=3)
        self.deps = SimpleNamespace(supabase=self.public, supabase_admin=self.admin,
                                    SUPABASE_URL="https://example.invalid")
        p = patch.dict(sys.modules, {"dependencies": self.deps})
        p.start()
        self.addCleanup(p.stop)
        p = patch.dict(users.users_db, {}, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def test_profile_and_history_operations_only_use_administrative_client(self):
        operations = (
            lambda: users.get_user(self.phone),
            lambda: users.get_user(self.phone, strict=True),
            lambda: users.get_user_id(self.phone),
            lambda: users.create_user_profile(self.phone),
            lambda: users.update_onboarding_profile(self.phone, {"email": "test@example.invalid"}),
            lambda: users.save_user(self.phone, self.user),
            lambda: users.update_user_fields(self.phone, {"rubro": "alimentos"}),
            lambda: users.reset_user_profile(self.phone, self.user),
            lambda: users.set_audio_consent(self.phone, True),
            lambda: users.is_user_blocked(self.phone),
            lambda: users.save_message(self.phone, "user", "test"),
            lambda: users.save_messages_by_user_id(self.user["id"], self.phone, [{"role": "user", "content": "test"}]),
            lambda: users.get_messages(self.phone),
            lambda: users.get_messages_by_user_id(self.user["id"]),
            lambda: users.get_last_user_message(self.phone),
            lambda: users.contar_mensajes(self.phone),
            lambda: users.count_messages_by_user_id(self.user["id"]),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                operation()
        self.public.table.assert_not_called()
        self.public.rpc.assert_not_called()
        self.query.eq.assert_any_call("phone", self.phone)
        self.query.eq.assert_any_call("user_id", self.user["id"])

    def test_backend_does_not_require_a_public_client(self):
        self.deps.supabase = None
        self.assertEqual(users.get_user(self.phone), self.user)
        self.assertEqual(users.get_messages_by_user_id(self.user["id"]), [self.user])
        self.assertEqual(users.count_messages_by_user_id(self.user["id"]), 3)

    def test_missing_admin_never_uses_public_client_or_memory(self):
        self.deps.supabase_admin = None
        users.users_db[self.phone] = dict(self.user)
        for operation in (
            lambda: users.get_user(self.phone),
            lambda: users.get_user_id(self.phone),
            lambda: users.create_user_profile(self.phone),
            lambda: users.save_user(self.phone, {"rubro": "textil"}),
            lambda: users.save_messages_by_user_id(self.user["id"], self.phone, [{"role": "user", "content": "test"}]),
            lambda: users.get_messages(self.phone),
            lambda: users.contar_mensajes(self.phone),
        ):
            with self.subTest(operation=operation), self.assertRaises(users.UserPersistenceError):
                operation()
        self.assertEqual(users.users_db[self.phone], self.user)
        self.public.table.assert_not_called()

    def test_database_errors_are_not_empty_profiles_or_histories(self):
        self.query.execute.side_effect = RuntimeError("private rejected row")
        users.users_db[self.phone] = dict(self.user)
        for operation in (
            lambda: users.get_user(self.phone),
            lambda: users.get_user_id(self.phone),
            lambda: users.save_user(self.phone, {"rubro": "textil"}),
            lambda: users.get_messages(self.phone),
            lambda: users.get_last_user_message(self.phone),
            lambda: users.contar_mensajes(self.phone),
        ):
            with self.subTest(operation=operation), self.assertRaises(users.UserPersistenceError):
                operation()
        self.assertEqual(users.users_db[self.phone], self.user)

    def test_memory_only_when_supabase_is_completely_unconfigured(self):
        with patch.dict(sys.modules, {"dependencies": SimpleNamespace(supabase=None, supabase_admin=None)}):
            self.assertIsNone(get_backend_client())
            users.create_user_profile(self.phone)
            self.assertEqual(users.get_user(self.phone)["phone"], self.phone)

    def test_fund_alert_catalog_uses_admin_and_preserves_filters(self):
        self.query.execute.return_value = SimpleNamespace(data=[])
        self.assertEqual(get_alertas_fondos_proximos({"inicio_sii": "no"}), [])
        self.admin.table.assert_called_once_with("fondos")
        self.query.eq.assert_called_once_with("activo", True)
        self.public.table.assert_not_called()
