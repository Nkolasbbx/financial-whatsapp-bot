import unittest

from core.profile_validation import (
    ProfileValidationError, validate_address, validate_email, validate_name, validate_rut,
)


class ProfileValidationTests(unittest.TestCase):
    def test_names_accept_accents_apostrophes_and_short_names(self):
        for text, expected in (("  María   González  ", "María González"), ("Li", "Li"), ("Ana O'Neill", "Ana O'Neill")):
            self.assertEqual(validate_name(text), expected)

    def test_rejects_empty_names_greetings_numbers_and_control_messages(self):
        for text in (None, "", "Hola!", "sí", "12345", "menu_calendar", "¿Qué hago?", "Ana\nPérez"):
            with self.subTest(text=text), self.assertRaises(ProfileValidationError):
                validate_name(text)

    def test_rut_supports_common_formats_and_checks_digit(self):
        for text in ("12.345.678-5", "12345678-5", "123456785"):
            self.assertEqual(validate_rut(text), "12345678-5")
        for text in ("12.345.678-9", "0-0", "12345", "12x345678-5", None):
            with self.subTest(text=text), self.assertRaises(ProfileValidationError):
                validate_rut(text)

    def test_email_validates_format_without_claiming_ownership(self):
        self.assertEqual(validate_email(" Ana+negocio@EJEMPLO.CL "), "Ana+negocio@ejemplo.cl")
        for text in ("ana@", "ana@localhost", "ana..perez@ejemplo.cl", "ana perez@ejemplo.cl", "ana@-ejemplo.cl", None):
            with self.subTest(text=text), self.assertRaises(ProfileValidationError):
                validate_email(text)

    def test_address_allows_rural_locations_and_rejects_controls(self):
        self.assertEqual(validate_address(" Camino Rural s/n "), "Camino Rural s/n")
        for text in ("", "hola", "menu_roadmap", "12345", "x" * 201):
            with self.subTest(text=text), self.assertRaises(ProfileValidationError):
                validate_address(text)
