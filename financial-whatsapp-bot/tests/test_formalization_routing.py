"""Regresión: el diagnóstico no bloquea botones ni otras funcionalidades."""

import copy
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

    def press(self, prompt, action):
        """Reenvía el ID real del botón, igual que el webhook de WhatsApp."""
        button_id = next(key for key, _ in prompt["options"] if key.rsplit(":", 1)[-1] == action)
        result = self.route(button_id)
        self.assertNotEqual(result, "__AI_QUERY__", "Un botón del diagnóstico no debe enviarse a IA")
        return result

    def router_to_costs(self):
        prompt = self.route("menu_diagnostico")
        prompt = self.press(prompt, "local")
        prompt = self.press(prompt, "yes")
        prompt = self.press(prompt, "yes")
        prompt = self.press(prompt, "yes")
        for text in ("1500000", "100000", "0"):
            prompt = self.route(text)
        prompt = self.press(prompt, "yes")
        self.route("2026-11")
        prompt = self.route("0")
        prompt = self.press(prompt, "yes")
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "costs")
        return prompt

    def test_entry_exits_funds_finances_and_calendar(self):
        self.mocks["get_active_fund_session"].return_value = {"status": "selecting"}
        self.assertIn("Diagnóstico", self.route("menu_diagnostico")["body"])
        self.mocks["cancel_fund_session"].assert_called_once_with(USER["id"])
        self.mocks["clear_calendar_session"].assert_called_once_with(USER["id"])
        self.mocks["clear_financial_session"].assert_called_once_with(USER["id"])

    def test_location_buttons_advance_without_pausing_or_sending_to_ai(self):
        for action in ("vivienda", "local", "otra"):
            with self.subTest(action=action):
                self.repo.data = {"diagnostic": None, "items": []}
                prompt = self.route("menu_diagnostico")
                result = self.press(prompt, action)
                d = self.repo.data["diagnostic"]
                self.assertEqual(d["location_type"], action)
                self.assertEqual(d["current_step"], "period")
                self.assertEqual(d["conversation_status"], "active")
                self.assertIn("mes observado", result["body"])

    def test_full_router_flow_confirms_without_sending_button_ids_to_ai(self):
        prompt = self.router_to_costs()
        prompt = self.press(prompt, "cost_C01")
        prompt = self.press(prompt, "yes")
        prompt = self.press(prompt, "costs_done")
        result = self.press(prompt, "confirm")
        self.assertIn("Parcial", result["body"])
        self.assertEqual(self.repo.data["diagnostic"]["workflow_status"], "confirmed")
        self.assertEqual(len(self.repo.revisions), 1)

    def test_result_buttons_show_details_sources_and_reopen_selected_field(self):
        prompt = self.router_to_costs()
        prompt = self.press(prompt, "costs_done")
        result = self.press(prompt, "confirm")
        self.assertIn("Costos iniciales", self.press(result, "details")["body"])
        self.assertIn("Fuentes del catálogo", self.press(result, "sources")["body"])
        previous = copy.deepcopy(self.repo.data["diagnostic"]["answers"])
        prompt = self.press(result, "edit")
        self.press(prompt, "edit_cash")
        self.route("2000000")
        d = self.repo.data["diagnostic"]
        self.assertEqual(d["current_step"], "review")
        self.assertEqual(d["answers"]["available_cash"], 2000000)
        self.assertEqual(d["answers"]["income_total"], previous["income_total"])
        self.assertEqual(d["answers"]["expenses_total"], previous["expenses_total"])
        self.assertEqual(len(self.repo.revisions), 1)

    def test_pause_button_stays_in_diagnostic_and_entry_resumes_same_step(self):
        prompt = self.route("menu_diagnostico")
        prompt = self.press(prompt, "local")
        self.assertIn("Conservé tu borrador", self.press(prompt, "pause"))
        self.assertEqual(self.repo.data["diagnostic"]["conversation_status"], "paused")
        self.route("retomar diagnóstico")
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "period")
        self.assertEqual(self.repo.data["diagnostic"]["conversation_status"], "active")

    def test_old_button_is_rejected_without_mutating_current_question(self):
        prompt = self.route("menu_diagnostico")
        self.press(prompt, "local")
        previous = copy.deepcopy(self.repo.data)
        self.assertIn("otra pregunta", self.press(prompt, "vivienda"))
        self.assertEqual(self.repo.data, previous)

    def test_diagnostic_ids_remain_navigation_for_other_modules(self):
        prompt = self.route("menu_diagnostico")
        self.assertTrue(message_router._is_explicit_other_module_action(prompt["options"][0][0]))

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
