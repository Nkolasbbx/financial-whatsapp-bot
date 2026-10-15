import copy
import unittest
from unittest.mock import Mock

from core.onboarding import SII_EXPLANATION, needs_onboarding, process_onboarding


def make_user(**overrides) -> dict:
    user = {
        "phone": "56900000000",
        "onboarding_step": 3,
        "rubro": "textil",
        "rubro_raw": "ropa",
        "comuna": "Recoleta",
        "nombre_completo": "María González",
        "rut": "12345678-5",
        "email": "maria@ejemplo.cl",
        "profile_completion_step": "done",
    }
    user.update(overrides)
    return user


def save_into(saved):
    def save(phone, changes):
        saved.append(copy.deepcopy(changes))
        return {"phone": phone, **changes}
    return save


class OnboardingSiiExplanationTests(unittest.TestCase):
    def test_no_sabe_shows_explanation_and_does_not_record_final_answer(self):
        user = make_user()
        saved = []

        result = process_onboarding(user, "no sé qué es eso", save_into(saved))

        self.assertEqual(user["onboarding_step"], "3_explicado")
        self.assertNotIn("inicio_sii", user)
        self.assertIn(SII_EXPLANATION, result["body"])
        # Se guardó el sub-estado, pero sin resolver inicio_sii todavía.
        self.assertEqual(len(saved), 1)
        self.assertNotIn("inicio_sii", saved[0])

    def test_after_explanation_yes_finalizes_as_formalized(self):
        user = make_user(onboarding_step="3_explicado")
        saved = []

        process_onboarding(user, "sii_si", save_into(saved))

        self.assertEqual(user["inicio_sii"], "si")
        self.assertEqual(user["onboarding_step"], "done")

    def test_after_explanation_no_finalizes_as_not_formalized_with_roadmap(self):
        user = make_user(onboarding_step="3_explicado")
        saved = []

        process_onboarding(user, "sii_no", save_into(saved))

        self.assertEqual(user["inicio_sii"], "no")
        self.assertEqual(user["onboarding_step"], "done")
        self.assertTrue(len(user["roadmap"]) > 0)

    def test_still_unsure_after_explanation_defaults_to_not_formalized(self):
        user = make_user(onboarding_step="3_explicado")
        saved = []

        process_onboarding(user, "no sé", save_into(saved))

        self.assertEqual(user["inicio_sii"], "no")
        self.assertEqual(user["onboarding_step"], "done")

    def test_back_from_explained_substate_returns_to_comuna(self):
        user = make_user(onboarding_step="3_explicado")
        saved = []

        result = process_onboarding(user, "volver", save_into(saved))

        self.assertEqual(user["onboarding_step"], "2")
        self.assertIn("comuna", result["body"])

    def test_ambiguous_answer_in_explained_substate_reprompts_without_saving(self):
        user = make_user(onboarding_step="3_explicado")
        saved = []

        process_onboarding(user, "hola", save_into(saved))

        self.assertEqual(user["onboarding_step"], "3_explicado")
        self.assertNotIn("inicio_sii", user)
        self.assertEqual(len(saved), 0)


class ProfileOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.user = {"phone": "+56900000000", "onboarding_step": "0"}
        self.saved = []

    def send(self, message):
        return process_onboarding(self.user, message, save_into(self.saved))

    def test_new_user_completes_personal_and_business_data(self):
        sequence = (
            ("hola", "nombre_completo"),
            ("María González", "rut"),
            ("12.345.678-5", "email"),
            ("maria@EJEMPLO.CL", "direccion_negocio"),
        )
        for message, next_field in sequence:
            self.send(message)
            self.assertEqual(self.user["profile_completion_step"], next_field)
        self.send("profile_skip_address")
        self.assertIsNone(self.user["direccion_negocio"])
        self.assertEqual(self.user["onboarding_step"], "1")
        self.send("rubro_alimentos")
        self.send("comuna_recoleta")
        result = self.send("sii_no")
        self.assertEqual(self.user["rut"], "12345678-5")
        self.assertEqual(self.user["email"], "maria@ejemplo.cl")
        self.assertEqual(self.user["onboarding_step"], "done")
        self.assertTrue(self.user["roadmap"])
        self.assertIn("Perfil completado", result["body"])
        self.assertFalse(needs_onboarding(self.user))

    def test_invalid_answer_or_old_button_does_not_change_state(self):
        self.send("hola")
        for message in ("hola", "menu_listo", "1234", "", "fund_answer:yes"):
            original = copy.deepcopy(self.user)
            count = len(self.saved)
            self.send(message)
            self.assertEqual(self.user, original)
            self.assertEqual(len(self.saved), count)
        self.send("Ana")
        for message in ("12.345.678-9", "menu_fondo", "omitir"):
            self.send(message)
            self.assertEqual(self.user["profile_completion_step"], "rut")
            self.assertNotIn("rut", self.user)
        self.send("12345678-5")
        self.send("ana@")
        self.assertEqual(self.user["profile_completion_step"], "email")

    def test_existing_user_only_completes_missing_data_and_preserves_progress(self):
        self.user = make_user(
            onboarding_step="done", nombre_completo=None, rut=None, email=None,
            profile_completion_step=None, inicio_sii="no", id="user-1", auth_user_id="auth-1",
            created_at="2026-01-01T00:00:00", resumen_conversacion="Historia anterior",
            roadmap=[{"title": "Primer paso", "done": True}, {"title": "Segundo", "done": False}],
            roadmap_completed_at=None, roadmap_completion_stats={"total_hitos": 2},
            reminder_count=2, next_reminder_at="2026-11-01T00:00:00Z",
            conversation_history=[{"role": "user", "content": "Hola"}],
        )
        original = copy.deepcopy(self.user)
        for text in ("mi roadmap", "Ana Pérez", "12345678-5", "ana@ejemplo.cl", "omitir"):
            self.send(text)
        allowed = {"nombre_completo", "rut", "email", "direccion_negocio", "profile_completion_step"}
        for key, value in original.items():
            if key not in allowed:
                self.assertEqual(self.user[key], value, key)
        self.assertTrue(all(set(patch).issubset(allowed) for patch in self.saved))
        self.assertFalse(needs_onboarding(self.user))

    def test_valid_stored_fields_are_not_asked_again(self):
        self.user = make_user(onboarding_step="done", inicio_sii="no", email=None, profile_completion_step=None,
                              direccion_negocio="Av. Recoleta 123")
        self.send("hola")
        self.assertEqual(self.user["profile_completion_step"], "email")
        self.send("maria@ejemplo.cl")
        self.assertFalse(needs_onboarding(self.user))
        self.assertNotIn("nombre_completo", self.saved[0])

    def test_legacy_business_session_accepts_pending_answer_before_personal_questions(self):
        for step in (2, "2"):
            with self.subTest(step=step):
                self.user = {"phone": "+56900000000", "onboarding_step": step, "rubro": "textil"}
                self.send("comuna_recoleta")
                self.assertEqual(self.user["comuna"], "Recoleta")
                self.assertEqual(self.user["onboarding_step"], "3")
                self.send("sii_no")
                self.assertEqual(self.user["inicio_sii"], "no")
                self.assertEqual(self.user["profile_completion_step"], "nombre_completo")
                self.assertNotIn("roadmap", self.user)

    def test_existing_business_values_are_skipped_after_personal_details(self):
        self.user = make_user(onboarding_step="0", inicio_sii="no", email=None,
                              profile_completion_step=None, direccion_negocio="Camino Rural s/n")
        self.send("hola")
        self.send("maria@ejemplo.cl")
        self.assertFalse(needs_onboarding(self.user))

    def test_back_allows_editing_without_discarding_other_answers(self):
        for text in ("hola", "María González", "12345678-5"):
            self.send(text)
        self.send("profile_back")
        self.assertEqual(self.user["profile_completion_step"], "rut")
        self.assertEqual(self.user["nombre_completo"], "María González")
        self.send("12345678-5")
        self.assertEqual(self.user["profile_completion_step"], "email")

    def test_address_can_be_saved_and_is_not_required_after_skipping(self):
        self.user = make_user(onboarding_step="done", inicio_sii="no", profile_completion_step="direccion_negocio")
        self.send("Av. Recoleta 123")
        self.assertEqual(self.user["direccion_negocio"], "Av. Recoleta 123")
        self.assertFalse(needs_onboarding(self.user))

    def test_missing_required_field_is_detected_even_if_completion_says_done(self):
        self.user = make_user(onboarding_step="done", inicio_sii="no", rut=None)
        self.assertTrue(needs_onboarding(self.user))
        self.send("hola")
        self.assertEqual(self.user["profile_completion_step"], "rut")

    def test_failed_save_does_not_advance_or_mutate_user_and_can_be_retried(self):
        self.send("hola")
        original = copy.deepcopy(self.user)
        for callback in (Mock(return_value=None), Mock(side_effect=RuntimeError("database down"))):
            result = process_onboarding(self.user, "Ana Pérez", callback)
            self.assertIn("No pude guardar", result["body"])
            self.assertEqual(self.user, original)
        self.send("Ana Pérez")
        self.assertEqual(self.user["profile_completion_step"], "rut")

    def test_failed_final_save_does_not_announce_completion_or_generate_roadmap(self):
        self.user = make_user(created_at="2026-01-01T00:00:00")
        original = copy.deepcopy(self.user)
        result = process_onboarding(self.user, "sii_no", Mock(return_value=None))
        self.assertIn("No pude guardar", result["body"])
        self.assertEqual(self.user, original)
        self.send("sii_no")
        self.assertEqual(self.user["created_at"], original["created_at"])
        self.assertTrue(self.user["roadmap"])

    def test_finalization_never_replaces_existing_roadmap(self):
        roadmap = [{"title": "Mi paso", "done": True}]
        self.user = make_user(roadmap=roadmap, roadmap_completed_at="2026-02-01T00:00:00Z")
        self.send("sii_si")
        self.assertEqual(self.user["roadmap"], roadmap)
        self.assertEqual(self.user["roadmap_completed_at"], "2026-02-01T00:00:00Z")

    def test_negated_sii_answer_is_not_confused_with_formalized(self):
        self.user = make_user()
        self.send("no tengo inicio de actividades")
        self.assertEqual(self.user["inicio_sii"], "no")


if __name__ == "__main__":
    unittest.main()
