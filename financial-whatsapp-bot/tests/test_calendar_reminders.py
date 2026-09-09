import unittest
from unittest.mock import AsyncMock, patch

from services import calendar_reminders


class CalendarReminderTests(unittest.IsolatedAsyncioTestCase):
    @patch.object(calendar_reminders, "CALENDAR_REMINDERS_ENABLED", True)
    @patch.object(calendar_reminders, "mark_calendar_delivery_sent")
    @patch.object(calendar_reminders, "create_calendar_delivery")
    @patch.object(calendar_reminders, "get_due_calendar_events")
    @patch.object(calendar_reminders, "send_template", new_callable=AsyncMock)
    async def test_sends_due_event_and_marks_delivery(
        self,
        send_mock,
        due_mock,
        create_delivery_mock,
        mark_sent_mock,
    ):
        due_mock.return_value = [
            {
                "id": "event-1",
                "phone": "+56911111111",
                "description": "Renovar patente",
                "event_at": "2099-09-20T12:00:00+00:00",
                "reminder_at": "2099-09-20T12:00:00+00:00",
            }
        ]
        create_delivery_mock.return_value = "delivery-1"
        send_mock.return_value = {"messages": [{"id": "wamid.1"}]}

        result = await calendar_reminders.send_due_calendar_reminders()

        self.assertEqual(result["calendar_sent"], 1)
        send_mock.assert_awaited_once()
        mark_sent_mock.assert_called_once_with("delivery-1", "wamid.1")

    @patch.object(calendar_reminders, "CALENDAR_REMINDERS_ENABLED", False)
    async def test_disabled_calendar_does_not_query_database(self):
        with patch.object(calendar_reminders, "get_due_calendar_events") as due_mock:
            result = await calendar_reminders.send_due_calendar_reminders()

        self.assertEqual(result["calendar_status"], "disabled")
        due_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
