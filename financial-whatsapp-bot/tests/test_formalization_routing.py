"""Regresión: el diagnóstico no bloquea botones ni otras funcionalidades."""

import unittest
from unittest.mock import patch

from core.menu import get_menu_widget
from services import message_router
from tests.profile_fixtures import COMPLETE_PROFILE
from tests import test_formalization_flow as flow_helpers

USER = flow_helpers.USER


class DiagnosticRoutingTests(unittest.TestCase):
    send = flow_helpers.FlowTests.send
    button = flow_helpers.FlowTests.button
    start = flow_helpers.FlowTests.start

    def setUp(self):
        flow_helpers.FlowTests.setUp(self)
        self.user = {**COMPLETE_PROFILE, **USER, "phone": "+56911111111", "onboarding_step": "done", "reminder_count": 0}
        self.mocks = {}
        for name, value in {"get_user": self.user, "handle_expert_message": None,
                            "get_active_fund_session": None, "get_requirement_definitions": {},
                            "get_calendar_session": None, "get_financial_session": None,
                            "cancel_fund_session": None, "clear_calendar_session": None,
                            "clear_financial_session": None, "record_roadmap_activity": None,
                            "record_incoming_reminder_reply": False}.items():
            p = patch.object(message_router, name, return_value=value)
            self.mocks[name] = p.start()
            self.addCleanup(p.stop)
        p = patch.object(message_router, "FORMALIZATION_DIAGNOSTICS_ENABLED", True)
        p.start()
        self.addCleanup(p.stop)

    def route(self, message):
        return message_router.route_message(self.user["phone"], message)

    def test_entry_exits_funds_finances_and_calendar(self):
        self.mocks["get_active_fund_session"].return_value = {"status": "selecting"}
        self.assertIn("Diagnóstico", self.route("menu_diagnostico")["body"])
        self.mocks["cancel_fund_session"].assert_called_once_with(USER["id"])
        self.mocks["clear_calendar_session"].assert_called_once_with(USER["id"])
        self.mocks["clear_financial_session"].assert_called_once_with(USER["id"])

    def test_hardcoded_buttons_reach_original_handlers(self):
        self.start()
        for message, expected in (("menu_financial", "menu"), ("menu_roadmap", "roadmap"),
                                  ("menu_panel_web", "__WEB_PANEL_LINK__"), ("hito_listo", "done")):
            self.send("retomar diagnóstico")
            with patch.object(message_router, "get_menu_widget", return_value="menu"), patch.object(
                message_router, "get_roadmap_text", return_value="roadmap",
            ), patch.object(message_router, "mark_hito_done", return_value="done"):
                self.assertEqual(self.route(message), expected)
            self.assertEqual(self.repo.data["diagnostic"]["conversation_status"], "paused")

    def test_unrelated_question_not_caught_by_formalizar_substring(self):
        self.start()
        with patch.object(message_router, "get_roadmap_text") as roadmap:
            self.assertEqual(self.route("¿Qué me falta para formalizar?"), "__AI_QUERY__")
            roadmap.assert_not_called()

    def test_expert_keeps_priority_and_pauses_diagnostic(self):
        self.start()
        self.mocks["handle_expert_message"].return_value = "__EXPERT_RELAY__"
        self.assertEqual(self.route("Hablar con un experto"), "__EXPERT_RELAY__")
        self.assertEqual(self.repo.data["diagnostic"]["conversation_status"], "paused")

    def test_reminder_submenu_cancels_fund_question(self):
        self.mocks["get_active_fund_session"].return_value = {"status": "selecting"}
        result = self.route("menu_recordatorios")
        self.assertEqual(len(result["options"]), 3)
        self.mocks["cancel_fund_session"].assert_called_once_with(USER["id"])

    def test_menu_with_diagnostic_stays_within_meta_limits(self):
        for user in (self.user, {**self.user, "inicio_sii": "si"}):
            menu = get_menu_widget(user)
            self.assertLessEqual(len(menu["body"]), 1024)
            self.assertLessEqual(len(menu["options"]), 10)
        self.assertIn("menu_diagnostico", dict(get_menu_widget(self.user)["options"]))

    def test_disabling_flag_leaves_existing_routes_available(self):
        with patch.object(message_router, "FORMALIZATION_DIAGNOSTICS_ENABLED", False), patch.object(
            message_router, "handle_diagnostic_message",
        ) as handler:
            self.assertEqual(self.route("menu_panel_web"), "__WEB_PANEL_LINK__")
            handler.assert_not_called()


if __name__ == "__main__":
    unittest.main()
