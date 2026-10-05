"""Pruebas del flujo de fondos no bloqueante, sin conexiones externas."""

import unittest
from unittest.mock import patch

from core.financial_flow import FINANCIAL_PARSE_TASK
from services import message_router


class FundRoutingTests(unittest.TestCase):
    def setUp(self):
        self.user = {
            "id": "user-1", "phone": "+56911111111",
            "onboarding_step": "done", "inicio_sii": "no", "reminder_count": 0,
            "roadmap": [{"title": "Primer paso", "done": False}],
        }
        self.mocks = {}
        for name, value in {
            "FINANCIAL_MOVEMENTS_ENABLED": True,
            "get_user": self.user,
            "get_active_fund_session": None,
            "get_requirement_definitions": {},
            "get_calendar_session": None,
            "get_financial_session": None,
            "cancel_fund_session": None,
            "clear_calendar_session": None,
            "clear_financial_session": None,
            "record_roadmap_activity": None,
            "record_incoming_reminder_reply": False,
            "handle_fund_message": "evaluación",
        }.items():
            patcher = patch.object(message_router, name, return_value=value)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def _pending(self, answer_type="boolean", *, update=False):
        session = {
            "status": "collecting_data", "pending_field_key": "dato",
            "fondo_id": None if update else "fund-1",
        }
        definition = {"answer_type": answer_type, "source_type": "user_answer"}
        self.mocks["get_active_fund_session"].return_value = session
        self.mocks["get_requirement_definitions"].return_value = {"dato": definition}
        return session, definition

    def test_question_during_selection_cancels_session_and_reaches_ai(self):
        self.mocks["get_active_fund_session"].return_value = {"status": "selecting"}
        result = message_router.route_message(self.user["phone"], "¿Cómo hago mi pitch?")
        self.assertEqual(result, "__AI_QUERY__")
        self.mocks["cancel_fund_session"].assert_called_once_with("user-1")
        self.mocks["handle_fund_message"].assert_not_called()
        self.mocks["get_requirement_definitions"].assert_not_called()

    def test_question_during_collection_or_edit_does_not_modify_answers(self):
        for update in (False, True):
            with self.subTest(update=update):
                self._pending(update=update)
                self.mocks["cancel_fund_session"].reset_mock()
                result = message_router.route_message(self.user["phone"], "¿Qué es un pitch?")
                self.assertEqual(result, "__AI_QUERY__")
                self.mocks["cancel_fund_session"].assert_called_once_with("user-1")
        self.mocks["handle_fund_message"].assert_not_called()

    def test_question_with_roadmap_trigger_does_not_mark_or_show_milestones(self):
        self._pending()
        with patch.object(message_router, "mark_hito_done") as mark, patch.object(
            message_router, "get_roadmap_text",
        ) as roadmap:
            for message in (
                "¿Qué me falta para formalizar?", "¿Qué hago después del siguiente paso?", "¿Listo?",
            ):
                result = message_router.route_message(self.user["phone"], message)
                self.assertEqual(result, "__AI_QUERY__")
            mark.assert_not_called()
            roadmap.assert_not_called()

    def test_f29_question_is_not_saved_as_numeric_answer(self):
        self._pending("number")
        result = message_router.route_message(self.user["phone"], "¿Cómo declaro el F29?")
        self.assertEqual(result, "__AI_QUERY__")
        self.mocks["handle_fund_message"].assert_not_called()

    def test_valid_answers_and_unclear_attempts_reuse_loaded_context(self):
        for answer_type, messages in (
            ("boolean", ("sí", "no", "no sé", "fund_answer:yes", "tal vez")),
            ("number", ("1.500 UF", "250,5 UF", "-5", "1,2,3")),
        ):
            for message in messages:
                with self.subTest(message=message):
                    session, definition = self._pending(answer_type)
                    for mock in self.mocks.values():
                        mock.reset_mock()
                    result = message_router.route_message(self.user["phone"], message)
                    self.assertEqual(result, "evaluación")
                    self.mocks["get_active_fund_session"].assert_called_once_with("user-1")
                    self.mocks["get_requirement_definitions"].assert_called_once_with(["dato"])
                    self.mocks["handle_fund_message"].assert_called_once_with(
                        self.user, message, session, definition,
                    )
                    self.mocks["cancel_fund_session"].assert_not_called()

    def test_other_module_buttons_still_work_and_cancel_funds(self):
        for message, expected in (
            ("menu_financial", "menú"), ("menu_roadmap", "roadmap"),
            ("hito_ayuda", "__AI_QUERY_WITH_CONTEXT__"),
            ("menu_panel_web", "__WEB_PANEL_LINK__"),
            ("unsatisfied_reformulate", "__AI_QUERY_WITH_REFORMULATE__"),
            ("ver más información", "Cómo declarar el F29"),
        ):
            with self.subTest(message=message):
                self._pending()
                self.mocks["cancel_fund_session"].reset_mock()
                with patch.object(message_router, "get_menu_widget", return_value="menú"), patch.object(
                    message_router, "get_roadmap_text", return_value="roadmap",
                ):
                    result = message_router.route_message(self.user["phone"], message)
                self.assertIn(expected, result)
                self.mocks["cancel_fund_session"].assert_called_once_with("user-1")
                self.mocks["handle_fund_message"].assert_not_called()

    def test_calendar_and_financial_entries_cancel_funds_once(self):
        self._pending()
        with patch.object(message_router, "handle_calendar_message", return_value="calendario"):
            result = message_router.route_message(self.user["phone"], "menu_calendar")
        self.assertEqual(result, "calendario")
        self.mocks["cancel_fund_session"].assert_called_once_with("user-1")
        self.mocks["cancel_fund_session"].reset_mock()
        result = message_router.route_message(self.user["phone"], "hoy vendí $40.000 en empanadas")
        self.assertEqual(result, FINANCIAL_PARSE_TASK)
        self.mocks["cancel_fund_session"].assert_called_once_with("user-1")

    def test_pause_and_reset_commands_close_funds_before_early_return(self):
        self._pending()
        with patch.object(message_router, "disable_reminders", return_value=True):
            result = message_router.route_message(self.user["phone"], "menu_recordatorios_off")
        self.assertIn("pausados", result["body"])
        self.mocks["cancel_fund_session"].assert_called_once_with("user-1")
        self.mocks["cancel_fund_session"].reset_mock()
        with patch.object(message_router, "reset_user_profile", return_value={"onboarding_step": 0}), patch.object(
            message_router, "process_onboarding", return_value="onboarding",
        ):
            self.assertEqual(
                message_router.route_message(self.user["phone"], "menu_reiniciar"), "onboarding",
            )
        self.mocks["cancel_fund_session"].assert_called_once_with("user-1")

    def test_question_that_mentions_funds_without_session_reaches_ai(self):
        result = message_router.route_message(self.user["phone"], "¿Cómo postular a fondos?")
        self.assertEqual(result, "__AI_QUERY__")
        self.mocks["handle_fund_message"].assert_not_called()

    def test_fund_entry_exits_other_drafts(self):
        self.mocks["get_calendar_session"].side_effect = (
            lambda _: None if self.mocks["clear_calendar_session"].called else {"state": "waiting_date"}
        )
        result = message_router.route_message(self.user["phone"], "evaluación de fondos")
        self.assertEqual(result, "evaluación")
        self.mocks["clear_calendar_session"].assert_called_once_with("user-1")
        self.mocks["clear_financial_session"].assert_called_once_with("user-1")

    def test_old_answer_button_leaves_other_module_drafts_untouched(self):
        self.mocks["get_calendar_session"].return_value = {"state": "waiting_date"}
        result = message_router.route_message(self.user["phone"], "fund_answer:yes")
        self.assertEqual(result, "evaluación")
        self.mocks["handle_fund_message"].assert_called_once_with(
            self.user, "fund_answer:yes", None, None,
        )
        self.mocks["get_calendar_session"].assert_not_called()
        self.mocks["clear_calendar_session"].assert_not_called()
        self.mocks["clear_financial_session"].assert_not_called()

    def test_session_read_failure_does_not_process_answer_as_another_action(self):
        self.mocks["get_active_fund_session"].side_effect = RuntimeError("database unavailable")
        result = message_router.route_message(self.user["phone"], "sí")
        self.assertIn("No pude procesar", result)
        self.mocks["handle_fund_message"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
