import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from core import calendar_flow
from services.message_router import route_message


class CalendarFlowTests(unittest.TestCase):
    def setUp(self):
        self.user = {
            "id": "user-1",
            "phone": "+56911111111",
            "reminders_enabled": True,
        }

    def test_date_parser_accepts_chilean_date_and_converts_to_utc(self):
        parsed = calendar_flow._parse_event_at(
            "20/09/2099 15:30",
            now=datetime(2099, 9, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(parsed.tzinfo, timezone.utc)
        self.assertEqual(calendar_flow._format_event_at(parsed), "20/09/2099 a las 15:30")

    def test_create_command_starts_persistent_session(self):
        with patch.object(calendar_flow, "start_calendar_session") as start_mock:
            result = calendar_flow.handle_calendar_message(
                self.user,
                "crear fecha importante",
            )

        start_mock.assert_called_once_with("user-1", "waiting_date")
        self.assertEqual(result["type"], "buttons")
        self.assertIn("DD/MM/AAAA", result["body"])

    def test_waiting_date_saves_draft_and_asks_description(self):
        session = {"state": "waiting_date"}
        with patch.object(calendar_flow, "update_calendar_session") as update_mock:
            result = calendar_flow.handle_calendar_message(
                self.user,
                "20/09/2099",
                session,
            )

        self.assertEqual(update_mock.call_args.kwargs["state"], "waiting_description")
        self.assertEqual(result["type"], "buttons")
        self.assertIn("descripción", result["body"])

    def test_question_interrupts_session_waiting_for_date(self):
        decision = calendar_flow.classify_calendar_input(
            "¿Cómo puedo sacar mi patente?",
            {"state": "waiting_date"},
        )

        self.assertEqual(decision, calendar_flow.CALENDAR_INPUT_INTERRUPT)

    def test_invalid_date_like_input_stays_in_calendar(self):
        decision = calendar_flow.classify_calendar_input(
            "32/15/2026",
            {"state": "waiting_date"},
        )

        self.assertEqual(decision, calendar_flow.CALENDAR_INPUT_HANDLE)

    def test_task_text_is_accepted_as_description(self):
        decision = calendar_flow.classify_calendar_input(
            "Renovar patente municipal",
            {"state": "waiting_description"},
        )

        self.assertEqual(decision, calendar_flow.CALENDAR_INPUT_HANDLE)

    def test_question_interrupts_session_waiting_for_description(self):
        decision = calendar_flow.classify_calendar_input(
            "¿Cómo renuevo mi patente municipal?",
            {"state": "waiting_description"},
        )

        self.assertEqual(decision, calendar_flow.CALENDAR_INPUT_INTERRUPT)

    def test_unexpected_text_interrupts_confirmation(self):
        decision = calendar_flow.classify_calendar_input(
            "Tengo otra consulta",
            {"state": "confirming_creation"},
        )

        self.assertEqual(decision, calendar_flow.CALENDAR_INPUT_INTERRUPT)

    def test_cancel_text_remains_a_calendar_control(self):
        decision = calendar_flow.classify_calendar_input(
            "cancelar",
            {"state": "waiting_description"},
        )

        self.assertEqual(decision, calendar_flow.CALENDAR_INPUT_HANDLE)

    @patch.object(calendar_flow, "clear_calendar_session")
    @patch.object(calendar_flow, "create_calendar_event")
    def test_confirmation_creates_event_and_clears_session(
        self,
        create_mock,
        clear_mock,
    ):
        create_mock.return_value = {"id": "event-1"}
        session = {
            "state": "confirming_creation",
            "draft_event_at": "2099-09-20T18:30:00+00:00",
            "draft_description": "Renovar patente",
        }

        result = calendar_flow.handle_calendar_message(
            self.user,
            calendar_flow.CALENDAR_CONFIRM_CREATE_ID,
            session,
        )

        create_mock.assert_called_once()
        clear_mock.assert_called_once_with("user-1")
        self.assertIn("Fecha guardada", result["body"])

    @patch.object(calendar_flow, "clear_calendar_session")
    @patch.object(calendar_flow, "get_active_calendar_events")
    def test_calendar_list_is_chronological_and_selectable(
        self, events_mock, clear_mock
    ):
        events_mock.return_value = [
            {
                "id": "event-1",
                "description": "Pagar patente",
                "event_at": "2099-09-20T12:00:00+00:00",
            },
            {
                "id": "event-2",
                "description": "Enviar formulario",
                "event_at": "2099-09-21T12:00:00+00:00",
            },
        ]

        result = calendar_flow.handle_calendar_message(
            self.user,
            "ver mi calendario",
        )

        self.assertEqual(result["type"], "list")
        self.assertEqual(result["options"][0][0], "calendar_event:event-1")
        self.assertLess(result["body"].index("Pagar patente"), result["body"].index("Enviar formulario"))

    @patch.object(calendar_flow, "clear_calendar_session")
    @patch.object(calendar_flow, "cancel_calendar_event")
    def test_delete_confirmation_soft_deletes_event(self, cancel_mock, clear_mock):
        cancel_mock.return_value = {"id": "event-1", "status": "cancelled"}
        session = {"state": "confirming_delete", "event_id": "event-1"}
        with patch.object(calendar_flow, "get_active_calendar_events", return_value=[]):
            result = calendar_flow.handle_calendar_message(
                self.user,
                calendar_flow.CALENDAR_CONFIRM_DELETE_ID,
                session,
            )

        cancel_mock.assert_called_once_with("user-1", "event-1")
        clear_mock.assert_called_once_with("user-1")
        self.assertIn("eliminada", result["body"])

    @patch("services.message_router.cancel_fund_session")
    @patch("services.message_router.handle_calendar_message")
    @patch("services.message_router.get_calendar_session", return_value=None)
    @patch("services.message_router.get_user")
    def test_message_router_prioritizes_calendar_entry(
        self,
        get_user_mock,
        _session_mock,
        handle_mock,
        cancel_fund_mock,
    ):
        get_user_mock.return_value = {
            **self.user,
            "onboarding_step": "done",
            "reminder_count": 0,
        }
        handle_mock.return_value = {"type": "text", "body": "calendario"}

        result = route_message(self.user["phone"], "menu_calendar")

        self.assertEqual(result["body"], "calendario")
        handle_mock.assert_called_once()
        cancel_fund_mock.assert_called_once_with("user-1")

    @patch("services.message_router.clear_calendar_session")
    @patch("services.message_router.handle_calendar_message")
    @patch(
        "services.message_router.get_calendar_session",
        return_value={"state": "waiting_date"},
    )
    @patch("services.message_router.get_user")
    def test_message_router_cancels_calendar_and_continues_to_ai(
        self,
        get_user_mock,
        _session_mock,
        handle_mock,
        clear_mock,
    ):
        get_user_mock.return_value = {
            **self.user,
            "onboarding_step": "done",
            "reminder_count": 0,
        }

        result = route_message(
            self.user["phone"],
            "¿Cómo puedo sacar mi patente?",
        )

        self.assertEqual(result, "__AI_QUERY_CALENDAR_CANCELLED__")
        clear_mock.assert_called_once_with("user-1")
        handle_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
