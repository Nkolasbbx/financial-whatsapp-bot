"""Acciones que cambian datos, pedidas por AUDIO, esperan un sí/no (HdU011, AC3)."""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from services import message_router
from services.message_router import CONFIRM_PREFIX, route_message
from tests.profile_fixtures import COMPLETE_PROFILE

PHONE = "+56911111111"


def _user():
    return {
        **COMPLETE_PROFILE,
        "phone": PHONE,
        "onboarding_step": "done",
        "roadmap": [
            {"title": "Primer hito", "desc": "Completar", "done": False},
            {"title": "Segundo hito", "desc": "Completar", "done": False},
        ],
    }


@patch("services.message_router.record_roadmap_activity")
# Marcar/deshacer un hito guarda solo las columnas del roadmap (optimización
# P-05) con _save_roadmap_fields, no el perfil completo con save_user.
@patch("services.message_router._save_roadmap_fields")
@patch("services.message_router.get_user")
class RouterConfirmationTests(unittest.TestCase):
    def test_done_by_audio_asks_instead_of_marking(self, get_user, save_user, _activity):
        get_user.return_value = _user()
        result = route_message(PHONE, "ya está, y ahora qué sigue", confirm_actions=True)
        self.assertEqual(result, CONFIRM_PREFIX + "listo")
        save_user.assert_not_called()  # el paso NO se marcó
        self.assertFalse(get_user.return_value["roadmap"][0]["done"])

    def test_done_without_audio_still_executes_directly(self, get_user, save_user, _activity):
        get_user.return_value = _user()
        route_message(PHONE, "listo")
        save_user.assert_called()
        self.assertTrue(get_user.return_value["roadmap"][0]["done"])

    def test_undo_by_audio_asks_first(self, get_user, save_user, _activity):
        get_user.return_value = _user()
        self.assertEqual(route_message(PHONE, "deshacer", confirm_actions=True), CONFIRM_PREFIX + "deshacer")
        save_user.assert_not_called()

    def test_confirmed_command_runs_the_real_action(self, get_user, save_user, _activity):
        # Tras el "sí", el webhook vuelve a llamar al router SIN confirm_actions.
        get_user.return_value = _user()
        route_message(PHONE, "listo")
        self.assertTrue(get_user.return_value["roadmap"][0]["done"])

    def test_reminders_by_audio_ask_first(self, get_user, _save, _activity):
        get_user.return_value = _user()
        with patch.object(message_router, "enable_reminders") as enable, \
             patch.object(message_router, "disable_reminders") as disable:
            self.assertEqual(
                route_message(PHONE, "activar recordatorios", confirm_actions=True),
                CONFIRM_PREFIX + "activar recordatorios",
            )
            self.assertEqual(
                route_message(PHONE, "pausar recordatorios", confirm_actions=True),
                CONFIRM_PREFIX + "pausar recordatorios",
            )
        enable.assert_not_called()
        disable.assert_not_called()

    def test_questions_do_not_trigger_a_confirmation(self, get_user, _save, _activity):
        get_user.return_value = _user()
        result = route_message(PHONE, "¿cómo declaro el IVA?", confirm_actions=True)
        self.assertFalse(isinstance(result, str) and result.startswith(CONFIRM_PREFIX))

    def test_every_confirmable_command_has_a_question(self, *_):
        for comando in ("listo", "deshacer", "activar recordatorios", "pausar recordatorios"):
            self.assertIn(comando, message_router.CONFIRM_QUESTIONS)
