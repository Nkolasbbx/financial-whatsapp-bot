import copy
import unittest
from unittest.mock import patch

from db.users import UserPersistenceError
from core import expert_flow
from services import message_router
from tests.profile_fixtures import COMPLETE_PROFILE


class OnboardingRoutingTests(unittest.TestCase):
    def setUp(self):
        self.user = {"id": "user-1", "phone": "+56900000000", "onboarding_step": "0"}
        self.saved = []
        self.mocks = {}
        specs = {
            "get_user": {"side_effect": lambda *args, **kwargs: copy.deepcopy(self.user)},
            "create_user_profile": {},
            "update_onboarding_profile": {"side_effect": self.save},
            "handle_expert_message": {"return_value": None},
            "get_active_fund_session": {"return_value": None},
            "get_calendar_session": {"return_value": None},
            "get_financial_session": {"return_value": None},
            "mark_hito_done": {},
            "record_incoming_reminder_reply": {"return_value": False},
            "disable_reminders": {"return_value": True},
        }
        for name, options in specs.items():
            patcher = patch.object(message_router, name, **options)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def save(self, phone, changes):
        self.assertEqual(phone, self.user["phone"])
        self.saved.append(copy.deepcopy(changes))
        self.user.update(changes)
        return copy.deepcopy(self.user)

    def send(self, text):
        return message_router.route_message(self.user["phone"], text)

    def test_end_to_end_onboarding_reloads_saved_progress_for_each_message(self):
        for text in ("hola", "Ana Pérez", "12345678-5", "ana@ejemplo.cl", "omitir", "rubro_textil", "comuna_el_bosque", "sii_no"):
            result = self.send(text)
            self.assertIsInstance(result, dict)
        self.assertEqual(self.user["onboarding_step"], "done")
        self.assertEqual(self.user["rubro"], "textil")
        self.assertEqual(self.user["comuna"], "El Bosque")
        self.assertTrue(self.user["roadmap"])
        self.mocks["get_active_fund_session"].assert_not_called()
        self.assertEqual(self.send("menu_panel_web"), "__WEB_PANEL_LINK__")

    def test_old_user_is_prompted_without_executing_triggering_milestone_button(self):
        self.user.update(onboarding_step="done", rubro="alimentos", comuna="Recoleta", inicio_sii="no",
                         roadmap=[{"title": "Paso", "done": False}])
        result = self.send("menu_listo")
        self.assertIn("nombre completo", result["body"])
        self.assertNotIn("nombre_completo", self.user)
        self.mocks["mark_hito_done"].assert_not_called()
        self.assertEqual(self.user["onboarding_step"], "done")

    def test_active_expert_conversation_has_priority_over_missing_personal_data(self):
        self.user.update(onboarding_step="done")
        with patch.object(message_router, "handle_expert_message", wraps=expert_flow.handle_expert_message), patch.object(
            expert_flow, "get_open_expert_request", return_value={"id": "request-1", "status": "in_progress"},
        ), patch.object(expert_flow, "add_expert_message") as relay, patch.object(expert_flow, "touch_user_message"):
            self.assertEqual(self.send("Necesito ayuda con mi patente"), "__EXPERT_RELAY__")
            relay.assert_called_once_with("request-1", "user", "Necesito ayuda con mi patente", wa_message_id=None)
        self.assertFalse(self.saved)

    def test_pause_reminders_remains_available_during_personal_question(self):
        self.user.update(onboarding_step="done", profile_completion_step="nombre_completo")
        result = self.send("menu_recordatorios_off")
        self.assertIn("pausados", result["body"])
        self.mocks["disable_reminders"].assert_called_once_with(self.user["phone"])
        self.assertFalse(self.saved)

    def test_complete_user_can_still_use_menu_and_ai(self):
        self.user.update(COMPLETE_PROFILE, onboarding_step="done")
        self.assertEqual(self.send("menu_financial")["type"], "list")
        self.assertEqual(self.send("¿Cómo puedo mejorar mis ventas?"), "__AI_QUERY__")
        self.assertFalse(self.saved)

    def test_old_profile_buttons_do_not_reopen_profile_or_call_ai(self):
        self.user.update(COMPLETE_PROFILE, onboarding_step="done")
        for message in ("profile_back", "profile_skip_address"):
            self.assertIn("pregunta anterior", self.send(message)["body"])
        self.assertFalse(self.saved)
        self.mocks["get_active_fund_session"].assert_not_called()

    def test_read_failure_does_not_create_another_profile(self):
        self.mocks["get_user"].side_effect = UserPersistenceError("network")
        self.assertIn("No pude consultar", self.send("hola"))
        self.mocks["create_user_profile"].assert_not_called()
        self.assertFalse(self.saved)

    def test_creation_failure_does_not_start_questions(self):
        self.mocks["get_user"].side_effect = None
        self.mocks["get_user"].return_value = None
        self.mocks["create_user_profile"].side_effect = UserPersistenceError("network")
        self.assertIn("No pude consultar o guardar", self.send("hola"))
        self.assertFalse(self.saved)

    def test_created_identity_is_used_for_following_updates(self):
        self.mocks["get_user"].side_effect = None
        self.mocks["get_user"].return_value = None
        self.mocks["create_user_profile"].return_value = copy.deepcopy(self.user)
        self.send("hola")
        self.mocks["create_user_profile"].assert_called_once_with(self.user["phone"])
        self.assertEqual(self.user["profile_completion_step"], "nombre_completo")
