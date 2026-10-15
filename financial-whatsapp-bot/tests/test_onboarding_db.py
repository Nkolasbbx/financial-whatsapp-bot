import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from db.users import (
    UserPersistenceError, create_user_profile, get_user, update_onboarding_profile, users_db,
)


class OnboardingPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.phone = "+56900000000"
        self.client = MagicMock()
        self.query = self.client.table.return_value
        self.query.update.return_value = self.query
        self.query.select.return_value = self.query
        self.query.eq.return_value = self.query
        self.query.limit.return_value = self.query
        self.query.upsert.return_value = self.query
        patcher = patch.dict(sys.modules, {"dependencies": SimpleNamespace(supabase=self.client)})
        patcher.start()
        self.addCleanup(patcher.stop)
        memory = patch.dict(users_db, {}, clear=True)
        memory.start()
        self.addCleanup(memory.stop)

    def test_updates_only_supplied_fields_without_upsert_or_history_changes(self):
        self.query.execute.return_value = SimpleNamespace(data=[{"phone": self.phone, "nombre_completo": "Ana Pérez"}])
        update_onboarding_profile(self.phone, {"nombre_completo": "Ana Pérez", "profile_completion_step": "rut"})
        payload = self.query.update.call_args.args[0]
        self.assertEqual(set(payload), {"nombre_completo", "profile_completion_step", "updated_at"})
        self.query.eq.assert_called_once_with("phone", self.phone)
        self.query.upsert.assert_not_called()

    def test_write_errors_never_fall_back_to_memory_or_log_personal_values(self):
        users_db[self.phone] = {"phone": self.phone, "nombre_completo": "Anterior"}
        self.query.execute.side_effect = RuntimeError("secret@example.cl was rejected")
        with self.assertLogs("financial", level="ERROR") as logs, self.assertRaises(UserPersistenceError):
            update_onboarding_profile(self.phone, {"email": "secret@example.cl"})
        self.assertEqual(users_db[self.phone]["nombre_completo"], "Anterior")
        self.assertNotIn("secret@example.cl", " ".join(logs.output))

    def test_missing_updated_row_is_a_failure(self):
        self.query.execute.return_value = SimpleNamespace(data=[])
        with self.assertRaises(UserPersistenceError):
            update_onboarding_profile(self.phone, {"profile_completion_step": "rut"})

    def test_unrelated_fields_cannot_be_updated_by_onboarding(self):
        for changes in ({"id": "other"}, {"reminder_count": 0}, {"resumen_conversacion": None}, {}):
            with self.assertRaises(ValueError):
                update_onboarding_profile(self.phone, changes)
        self.client.table.assert_not_called()

    def test_creation_does_not_replace_a_concurrently_created_user(self):
        original = {"phone": self.phone, "onboarding_step": "done", "roadmap": [{"done": True}]}
        self.query.execute.side_effect = [SimpleNamespace(data=[]), SimpleNamespace(data=[original])]
        self.assertEqual(create_user_profile(self.phone), original)
        self.assertTrue(self.query.upsert.call_args.kwargs["ignore_duplicates"])

    def test_strict_read_does_not_treat_error_as_a_new_or_cached_user(self):
        users_db[self.phone] = {"phone": self.phone}
        self.query.execute.side_effect = RuntimeError("network")
        with self.assertRaises(UserPersistenceError):
            get_user(self.phone, strict=True)
        self.query.upsert.assert_not_called()

    def test_empty_strict_read_does_not_return_stale_cache(self):
        users_db[self.phone] = {"phone": self.phone}
        self.query.execute.return_value = SimpleNamespace(data=[])
        self.assertIsNone(get_user(self.phone, strict=True))

    def test_configured_but_unavailable_client_does_not_use_memory(self):
        users_db[self.phone] = {"phone": self.phone}
        dependencies = SimpleNamespace(supabase=None, SUPABASE_URL="https://example.invalid")
        with patch.dict(sys.modules, {"dependencies": dependencies}):
            for operation in (
                lambda: get_user(self.phone, strict=True),
                lambda: create_user_profile(self.phone),
                lambda: update_onboarding_profile(self.phone, {"nombre_completo": "Ana"}),
            ):
                with self.assertRaises(UserPersistenceError):
                    operation()
        self.assertEqual(users_db[self.phone], {"phone": self.phone})
