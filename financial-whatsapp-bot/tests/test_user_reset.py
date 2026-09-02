import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from db.users import reset_user_profile
from services.message_router import route_message


class UserProfileResetTests(unittest.TestCase):
    def test_database_reset_nulls_functional_fields_and_preserves_identity(self):
        client = MagicMock()
        query = client.table.return_value
        query.update.return_value.eq.return_value.execute.return_value = SimpleNamespace(
            data=[{"id": "user-1", "phone": "+56911111111", "onboarding_step": 0}]
        )
        fake_dependencies = SimpleNamespace(
            supabase=client,
            supabase_admin=client,
        )
        current_user = {
            "id": "user-1",
            "phone": "+56911111111",
            "auth_user_id": "auth-1",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-08-31T00:00:00+00:00",
            "rubro": "alimentos",
            "comuna": "Recoleta",
            "inicio_sii": "no",
            "onboarding_step": "done",
            "roadmap": [{"title": "Hito", "done": False}],
            "resumen_conversacion": "Resumen anterior",
            "reminders_enabled": True,
            "reminders_paused": True,
            "reminder_count": 3,
        }

        with patch.dict(sys.modules, {"dependencies": fake_dependencies}):
            result = reset_user_profile("+56911111111", current_user)

        self.assertEqual(result["onboarding_step"], 0)
        payload = query.update.call_args.args[0]
        self.assertNotIn("id", payload)
        self.assertNotIn("phone", payload)
        self.assertNotIn("auth_user_id", payload)
        self.assertNotIn("created_at", payload)
        self.assertIsNone(payload["rubro"])
        self.assertIsNone(payload["comuna"])
        self.assertIsNone(payload["inicio_sii"])
        self.assertIsNone(payload["roadmap"])
        self.assertIsNone(payload["resumen_conversacion"])
        self.assertEqual(payload["onboarding_step"], 0)
        self.assertFalse(payload["reminders_enabled"])
        self.assertFalse(payload["reminders_paused"])
        self.assertEqual(payload["reminder_count"], 0)

    @patch("services.message_router.process_onboarding", return_value="onboarding")
    @patch("services.message_router.reset_user_profile")
    @patch("services.message_router.get_user")
    def test_reset_button_starts_onboarding_with_clean_user(
        self,
        get_user_mock,
        reset_profile_mock,
        process_onboarding_mock,
    ):
        current_user = {
            "id": "user-1",
            "phone": "+56911111111",
            "onboarding_step": "done",
            "rubro": "alimentos",
        }
        clean_user = {
            "id": "user-1",
            "phone": "+56911111111",
            "onboarding_step": 0,
            "rubro": None,
        }
        get_user_mock.return_value = current_user
        reset_profile_mock.return_value = clean_user

        result = route_message("+56911111111", "menu_reiniciar")

        self.assertEqual(result, "onboarding")
        reset_profile_mock.assert_called_once_with(
            "+56911111111",
            current_user,
        )
        process_onboarding_mock.assert_called_once_with(
            clean_user,
            "menu_reiniciar",
            unittest.mock.ANY,
        )


if __name__ == "__main__":
    unittest.main()
