import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from core.roadmaps import get_pending_milestone
from db.reminders import calculate_next_reminder_at
from services.reminders import select_reminder_template, send_due_reminders


class ReminderTests(unittest.IsolatedAsyncioTestCase):
    def test_next_reminder_is_three_days_later(self):
        current = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
        result = datetime.fromisoformat(calculate_next_reminder_at(current))

        self.assertEqual(result, datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc))

    def test_get_pending_milestone_returns_first_incomplete(self):
        user = {
            "roadmap": [
                {"title": "Primero", "done": True},
                {"title": "Segundo", "done": False},
                {"title": "Tercero", "done": False},
            ]
        }

        self.assertEqual(get_pending_milestone(user)["title"], "Segundo")

    @patch("services.reminders.REMINDER_FINAL_TEMPLATE_NAME", "last_reminder_roadmap")
    @patch("services.reminders.REMINDER_TEMPLATE_NAME", "recordatorio_roadmap")
    def test_first_and_second_reminders_use_normal_template(self):
        self.assertEqual(select_reminder_template(1), "recordatorio_roadmap")
        self.assertEqual(select_reminder_template(2), "recordatorio_roadmap")

    @patch("services.reminders.REMINDER_FINAL_TEMPLATE_NAME", "last_reminder_roadmap")
    def test_third_reminder_selects_final_template(self):
        self.assertEqual(select_reminder_template(3), "last_reminder_roadmap")

    @patch("services.reminders.REMINDER_RECIPIENT_LABEL", "emprendedor/a")
    @patch("services.reminders.REMINDER_TEMPLATE_LANGUAGE", "es_CL")
    @patch("services.reminders.REMINDER_FINAL_TEMPLATE_NAME", "last_reminder_roadmap")
    @patch("services.reminders.REMINDER_TEMPLATE_NAME", "recordatorio_roadmap")
    @patch("services.reminders.REMINDERS_ENABLED", True)
    @patch("services.reminders.mark_reminder_sent")
    @patch("services.reminders.create_reminder_delivery", return_value="delivery-1")
    @patch("services.reminders.get_due_reminder_users")
    @patch("services.reminders.send_template", new_callable=AsyncMock)
    async def test_third_reminder_uses_final_template(
        self,
        send_template_mock,
        get_users_mock,
        create_mock,
        mark_sent_mock,
        *_config_mocks,
    ):
        get_users_mock.return_value = [{
            "id": "user-1",
            "phone": "+56911111111",
            "roadmap": [{"title": "Inicio de actividades", "done": False}],
            "reminder_count": 2,
            "next_reminder_at": "2026-08-12T12:00:00+00:00",
        }]
        send_template_mock.return_value = {
            "messages": [{"id": "wamid.third"}],
        }

        result = await send_due_reminders()

        self.assertEqual(result["sent"], 1)
        create_mock.assert_called_once_with(
            "user-1",
            3,
            "Inicio de actividades",
            "last_reminder_roadmap",
            "2026-08-12T12:00:00+00:00",
        )
        send_template_mock.assert_awaited_once_with(
            "+56911111111",
            "last_reminder_roadmap",
            "es_CL",
            ["emprendedor/a", "Inicio de actividades"],
        )
        mark_sent_mock.assert_called_once_with(
            "delivery-1",
            "user-1",
            3,
            "wamid.third",
        )

    @patch("services.reminders.REMINDERS_ENABLED", False)
    async def test_scheduler_does_nothing_while_disabled(self):
        result = await send_due_reminders()

        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["sent"], 0)


if __name__ == "__main__":
    unittest.main()
