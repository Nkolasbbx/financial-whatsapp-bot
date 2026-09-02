import unittest
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from db.users import get_last_user_message
from db.reminders import record_roadmap_activity
from services.message_router import (
    detect_unsatisfaction,
    handle_unsatisfaction_choice,
    route_message,
)


class UnsatisfactionTests(unittest.TestCase):
    def test_positive_message_is_not_detected_as_unsatisfaction(self):
        self.assertFalse(detect_unsatisfaction("Sí, eso me sirve mucho"))
        self.assertFalse(detect_unsatisfaction("Gracias, eso me ayuda"))

    def test_negative_message_is_detected_as_unsatisfaction(self):
        self.assertTrue(detect_unsatisfaction("Eso no me sirvió"))
        self.assertTrue(detect_unsatisfaction("La respuesta no me ayuda"))

    def test_reformulate_button_does_not_persist_button_id(self):
        save_user_mock = MagicMock()
        user = {"phone": "+56911111111"}

        result = handle_unsatisfaction_choice(
            user["phone"],
            "unsatisfied_reformulate",
            "unsatisfied_reformulate",
            user,
            save_user_mock,
        )

        self.assertEqual(result, "__AI_QUERY_WITH_REFORMULATE__")
        self.assertNotIn("last_unsatisfied_message", user)
        self.assertNotIn("reformulate_attempt", user)
        save_user_mock.assert_not_called()

    @patch("db.users.get_user_id", return_value="user-uuid")
    def test_get_last_user_message_queries_only_latest_user_message(
        self,
        _get_user_id_mock,
    ):
        query = MagicMock()
        query.select.return_value = query
        query.eq.return_value = query
        query.order.return_value = query
        query.limit.return_value = query
        query.execute.return_value = SimpleNamespace(
            data=[{"content": "¿Cuál es el trámite correcto?"}]
        )
        supabase = MagicMock()
        supabase.table.return_value = query
        fake_dependencies = SimpleNamespace(supabase=supabase)

        with patch.dict(sys.modules, {"dependencies": fake_dependencies}):
            result = get_last_user_message("+56911111111")

        self.assertEqual(result, "¿Cuál es el trámite correcto?")
        supabase.table.assert_called_once_with("messages")
        query.select.assert_called_once_with("content")
        query.order.assert_called_once_with("created_at", desc=True)
        query.limit.assert_called_once_with(1)


class CompletedRoadmapReminderTests(unittest.TestCase):
    def test_completed_roadmap_activity_keeps_schedule_cleared(self):
        query = MagicMock()
        query.select.return_value = query
        query.eq.return_value = query
        query.limit.return_value = query
        query.update.return_value = query
        query.execute.side_effect = [
            SimpleNamespace(data=[{
                "reminders_enabled": True,
                "roadmap": [{"title": "Único", "done": True}],
            }]),
            SimpleNamespace(data=[]),
        ]
        supabase_admin = MagicMock()
        supabase_admin.table.return_value = query
        fake_dependencies = SimpleNamespace(supabase_admin=supabase_admin)

        with patch.dict(sys.modules, {"dependencies": fake_dependencies}):
            record_roadmap_activity("+56911111111")

        update_payload = query.update.call_args.args[0]
        self.assertTrue(update_payload["reminders_paused"])
        self.assertEqual(
            update_payload["reminders_pause_reason"],
            "manual",
        )
        self.assertEqual(update_payload["reminder_count"], 0)
        self.assertIsNone(update_payload["next_reminder_at"])

    @patch("services.message_router.record_roadmap_activity")
    @patch("services.message_router.clear_completed_roadmap_schedule_by_phone")
    @patch("services.message_router.save_user")
    @patch("services.message_router.get_user")
    def test_last_milestone_clears_reminder_schedule_immediately(
        self,
        get_user_mock,
        _save_user_mock,
        clear_schedule_mock,
        record_activity_mock,
    ):
        get_user_mock.return_value = {
            "phone": "+56911111111",
            "onboarding_step": "done",
            "roadmap": [
                {"title": "Inicio de actividades", "desc": "Completar", "done": False}
            ],
            "rubro": "alimentos",
            "comuna": "Santiago",
        }

        route_message("+56911111111", "listo")

        clear_schedule_mock.assert_called_once_with("+56911111111")
        record_activity_mock.assert_not_called()

    @patch("services.message_router.record_roadmap_activity")
    @patch("services.message_router.clear_completed_roadmap_schedule_by_phone")
    @patch("services.message_router.save_user")
    @patch("services.message_router.get_user")
    def test_intermediate_milestone_reschedules_normal_activity(
        self,
        get_user_mock,
        _save_user_mock,
        clear_schedule_mock,
        record_activity_mock,
    ):
        get_user_mock.return_value = {
            "phone": "+56911111111",
            "onboarding_step": "done",
            "roadmap": [
                {"title": "Primer hito", "desc": "Completar", "done": False},
                {"title": "Segundo hito", "desc": "Completar", "done": False},
            ],
        }

        route_message("+56911111111", "listo")

        record_activity_mock.assert_called_once_with("+56911111111")
        clear_schedule_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
