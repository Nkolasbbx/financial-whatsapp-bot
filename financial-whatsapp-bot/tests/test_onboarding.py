import unittest

from core.onboarding import SII_EXPLANATION, process_onboarding


def make_user(**overrides) -> dict:
    user = {
        "phone": "56900000000",
        "onboarding_step": 3,
        "rubro": "textil",
        "rubro_raw": "ropa",
        "comuna": "Recoleta",
    }
    user.update(overrides)
    return user


class OnboardingSiiExplanationTests(unittest.TestCase):
    def test_no_sabe_shows_explanation_and_does_not_record_final_answer(self):
        user = make_user()
        saved = []

        result = process_onboarding(user, "no sé qué es eso", lambda phone, u: saved.append(dict(u)))

        self.assertEqual(user["onboarding_step"], "3_explicado")
        self.assertNotIn("inicio_sii", user)
        self.assertIn(SII_EXPLANATION, result["body"])
        # Se guardó el sub-estado, pero sin resolver inicio_sii todavía.
        self.assertEqual(len(saved), 1)
        self.assertNotIn("inicio_sii", saved[0])

    def test_after_explanation_yes_finalizes_as_formalized(self):
        user = make_user(onboarding_step="3_explicado")
        saved = []

        process_onboarding(user, "sii_si", lambda phone, u: saved.append(dict(u)))

        self.assertEqual(user["inicio_sii"], "si")
        self.assertEqual(user["onboarding_step"], "done")

    def test_after_explanation_no_finalizes_as_not_formalized_with_roadmap(self):
        user = make_user(onboarding_step="3_explicado")
        saved = []

        process_onboarding(user, "sii_no", lambda phone, u: saved.append(dict(u)))

        self.assertEqual(user["inicio_sii"], "no")
        self.assertEqual(user["onboarding_step"], "done")
        self.assertTrue(len(user["roadmap"]) > 0)

    def test_still_unsure_after_explanation_defaults_to_not_formalized(self):
        user = make_user(onboarding_step="3_explicado")
        saved = []

        process_onboarding(user, "no sé", lambda phone, u: saved.append(dict(u)))

        self.assertEqual(user["inicio_sii"], "no")
        self.assertEqual(user["onboarding_step"], "done")

    def test_back_from_explained_substate_returns_to_comuna(self):
        user = make_user(onboarding_step="3_explicado")
        saved = []

        result = process_onboarding(user, "volver", lambda phone, u: saved.append(dict(u)))

        self.assertEqual(user["onboarding_step"], 2)
        self.assertIn("Comuna", result["body"])

    def test_ambiguous_answer_in_explained_substate_reprompts_without_saving(self):
        user = make_user(onboarding_step="3_explicado")
        saved = []

        process_onboarding(user, "hola", lambda phone, u: saved.append(dict(u)))

        self.assertEqual(user["onboarding_step"], "3_explicado")
        self.assertNotIn("inicio_sii", user)
        self.assertEqual(len(saved), 0)


if __name__ == "__main__":
    unittest.main()
