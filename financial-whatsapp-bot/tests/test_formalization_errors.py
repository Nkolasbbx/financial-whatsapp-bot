"""Clasificación y mensajes públicos sin driver, credenciales ni conexiones."""

import unittest

from core.formalization_errors import (
    formalization_error_code, formalization_error_message, safe_sqlstate,
)
from db.formalization_diagnostics import (
    FormalizationConflictError, FormalizationConnectionError, FormalizationNotFoundError,
)


class UnsafeError(RuntimeError):
    def __str__(self):
        raise AssertionError("Nunca se debe convertir el error original a texto")


def database_error(state):
    error = UnsafeError("postgresql://usuario:SECRETO@host/bd SELECT DATOS_PERSONALES")
    error.pgcode = state
    return error


class FormalizationErrorTests(unittest.TestCase):
    def test_sqlstate_categories_without_driver_or_database(self):
        cases = {
            "08006": "DIAG_CONNECTION", "28P01": "DIAG_CONNECTION", "28000": "DIAG_CONNECTION",
            "3D000": "DIAG_CONNECTION", "53300": "DIAG_CONNECTION", "57P01": "DIAG_CONNECTION",
            "42501": "DIAG_PERMISSION", "25006": "DIAG_PERMISSION",
            "42P01": "DIAG_SCHEMA", "42703": "DIAG_SCHEMA", "42883": "DIAG_SCHEMA",
            "42601": "DIAG_SCHEMA", "3F000": "DIAG_SCHEMA",
            "23502": "DIAG_CONSTRAINT", "23503": "DIAG_CONSTRAINT", "23514": "DIAG_CONSTRAINT",
            "23505": "DIAG_CONSTRAINT", "P0001": "DIAG_CONSTRAINT",
            "57014": "DIAG_TIMEOUT", "55P03": "DIAG_TIMEOUT",
            "40001": "DIAG_CONFLICT", "40P01": "DIAG_CONFLICT",
            "22023": "DIAG_VALIDATION", "22P02": "DIAG_VALIDATION", "XX000": "DIAG_INTERNAL",
        }
        for state, expected in cases.items():
            with self.subTest(state=state):
                self.assertEqual(formalization_error_code(database_error(state)), expected)

    def test_pool_unavailable_and_no_state_driver_failures_are_connections(self):
        self.assertEqual(formalization_error_code(FormalizationConnectionError("hidden")), "DIAG_CONNECTION")
        for name in ("OperationalError", "InterfaceError", "PoolError"):
            cls = type(name, (Exception,), {"__module__": "psycopg2.pool"})
            self.assertEqual(formalization_error_code(cls("hidden")), "DIAG_CONNECTION")
        self.assertEqual(formalization_error_code(ConnectionResetError("hidden")), "DIAG_CONNECTION")
        self.assertEqual(formalization_error_code(TimeoutError("hidden")), "DIAG_TIMEOUT")

    def test_cause_is_used_without_exposing_its_contents(self):
        wrapper = RuntimeError("hidden")
        wrapper.__cause__ = database_error("42703")
        self.assertEqual(formalization_error_code(wrapper), "DIAG_SCHEMA")
        self.assertEqual(safe_sqlstate(wrapper), "42703")
        self.assertIn("SQLSTATE: 42703", formalization_error_message(wrapper))

    def test_database_cause_not_mislabeled_as_user_input(self):
        wrapper = ValueError("hidden")
        wrapper.__cause__ = database_error("42501")
        self.assertEqual(formalization_error_code(wrapper), "DIAG_PERMISSION")

    def test_retryable_conflict_keeps_its_own_category(self):
        wrapper = FormalizationConflictError("hidden")
        wrapper.__cause__ = database_error("23505")
        self.assertEqual(formalization_error_code(wrapper), "DIAG_CONFLICT")

    def test_unknown_validation_and_not_found_have_safe_fallbacks(self):
        self.assertEqual(formalization_error_code(UnsafeError("hidden")), "DIAG_INTERNAL")
        self.assertEqual(formalization_error_code(ValueError("hidden")), "DIAG_VALIDATION")
        self.assertEqual(formalization_error_code(FormalizationNotFoundError("hidden")), "DIAG_VALIDATION")

    def test_public_messages_never_interpolate_exception_payload(self):
        for error in (database_error("23514"), ValueError("SECRETO SELECT DATOS_PERSONALES"), UnsafeError()):
            with self.subTest(error_type=type(error).__name__):
                text = formalization_error_message(error, "create")
                self.assertIn("Código: DIAG_", text)
                self.assertIn("inicio del diagnóstico", text)
                for forbidden in ("SECRETO", "SELECT", "DATOS_PERSONALES", "postgresql://", "usuario", "@host"):
                    self.assertNotIn(forbidden, text)

    def test_invalid_state_and_unknown_stage_are_not_echoed(self):
        for value in ("SECRETO largo", "42703\nSELECT", "42703-token", 42703, None, "abcde"):
            error = database_error(value)
            self.assertIsNone(safe_sqlstate(error))
            text = formalization_error_message(error, "SECRETO")
            self.assertNotIn("SQLSTATE:", text)
            self.assertNotIn("SECRETO", text)
            self.assertIn("Etapa: flujo del diagnóstico", text)

    def test_cyclic_exception_chain_terminates(self):
        first, second = RuntimeError(), database_error("23514")
        first.__cause__, second.__cause__ = second, first
        self.assertEqual(formalization_error_code(first), "DIAG_CONSTRAINT")
        self.assertEqual(safe_sqlstate(first), "23514")

    def test_postgrest_sqlstate_uses_code_attribute_without_raw_details(self):
        for code, expected in (("42P01", "DIAG_SCHEMA"), ("42501", "DIAG_PERMISSION"),
                               ("23514", "DIAG_CONSTRAINT"), ("40001", "DIAG_CONFLICT")):
            error = UnsafeError("SECRETO SELECT DATOS_PERSONALES")
            error.code = code
            with self.subTest(code=code):
                self.assertEqual(formalization_error_code(error), expected)
                self.assertEqual(safe_sqlstate(error), code)
                self.assertNotIn("SECRETO", formalization_error_message(error))

    def test_postgrest_transport_auth_and_schema_cache_codes(self):
        cases = {"PGRST000": "DIAG_CONNECTION", "PGRST001": "DIAG_CONNECTION",
                 "PGRST002": "DIAG_CONNECTION", "PGRST003": "DIAG_TIMEOUT",
                 "PGRST200": "DIAG_SCHEMA", "PGRST202": "DIAG_SCHEMA", "PGRST204": "DIAG_SCHEMA",
                 "PGRST205": "DIAG_SCHEMA", "PGRST301": "DIAG_PERMISSION", "PGRST303": "DIAG_PERMISSION"}
        for code, expected in cases.items():
            error = UnsafeError("SECRETO")
            error.code = code
            with self.subTest(code=code):
                self.assertEqual(formalization_error_code(error), expected)
                self.assertIsNone(safe_sqlstate(error))
                self.assertNotIn("SECRETO", formalization_error_message(error))

    def test_missing_rpc_has_specific_safe_hint(self):
        error = UnsafeError()
        error.code = "PGRST202"
        text = formalization_error_message(error)
        self.assertIn("Faltan las funciones del diagnóstico", text)

    def test_httpx_timeout_and_connection_without_importing_httpx(self):
        for name, expected in (("TimeoutException", "DIAG_TIMEOUT"), ("NetworkError", "DIAG_CONNECTION")):
            cls = type(name, (Exception,), {"__module__": "httpx"})
            self.assertEqual(formalization_error_code(cls()), expected)


if __name__ == "__main__":
    unittest.main()
