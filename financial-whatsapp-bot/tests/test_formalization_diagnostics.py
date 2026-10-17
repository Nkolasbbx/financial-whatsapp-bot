"""Contratos y transporte RPC, sin credenciales, red ni conexiones del RAG.

La semántica SQL se prueba aparte en local_testing/check_formalization_rpc.sql;
un cliente simulado no demuestra que la función se haya instalado en Supabase.
"""

import math
import sys
import types
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from db import formalization_diagnostics as db
from schemas.formalization_diagnostics import (
    FormalizationAssessmentInput, FormalizationDraftPatch, FormalizationItemInput,
)

USER = "11111111-1111-4111-8111-111111111111"
DIAG = "33333333-3333-4333-8333-333333333333"
REQUEST = "44444444-4444-4444-8444-444444444444"
CATALOG = "55555555-5555-4555-8555-555555555555"
DAY = date(2026, 10, 16)


def item(**changes):
    return FormalizationItemInput.model_validate(dict({
        "item_key": "rent", "label": "Arriendo", "item_kind": "current_operating",
        "economic_nature": "expense", "cash_direction": "outflow", "source_type": "user",
    }, **changes))


def context():
    return {"diagnostic": {"id": DIAG, "user_id": USER, "lock_version": 3}, "items": []}


def assessment():
    return {"assessment_status": "partial", "result": {"pending": True}}


class SchemaTests(unittest.TestCase):
    def test_patch_omission_does_not_replace_answers(self):
        self.assertEqual(FormalizationDraftPatch(current_step="expenses").model_dump(exclude_unset=True),
                         {"current_step": "expenses"})

    def test_result_and_owner_cannot_be_written_as_answers_patch(self):
        for field in ("result", "capacity", "user_id", "source_fingerprint", "catalog_fingerprint"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                FormalizationDraftPatch.model_validate({field: "forbidden"})

    def test_unknown_amount_stays_none(self):
        self.assertIsNone(item().amount_clp_min)
        self.assertIsNone(item().amount_paid_clp)

    def test_zero_is_distinct_from_unknown(self):
        self.assertEqual(item(amount_clp_min=0, amount_clp_max=0, amount_state="confirmed").amount_clp_min, 0)

    def test_invalid_amounts_are_rejected(self):
        examples = [
            {"amount_clp_min": 1}, {"amount_clp_min": 10, "amount_clp_max": 9},
            {"amount_clp_min": True, "amount_clp_max": 1},
            {"amount_clp_min": 1.5, "amount_clp_max": 2},
            {"amount_clp_min": -1, "amount_clp_max": 2},
            {"amount_clp_min": 0, "amount_clp_max": 2, "amount_paid_clp": 3},
            {"amount_original": "NaN", "unit": "UF"}, {"amount_original": "Infinity", "unit": "UF"},
            {"amount_original": "0.1234567", "unit": "UF"}, {"amount_original": "0.26"},
            {"amount_state": "quoted"},
        ]
        for example in examples:
            with self.subTest(example=example), self.assertRaises(ValidationError):
                item(**example)

    def test_decimal_unit_amount_preserves_precision(self):
        self.assertEqual(item(amount_original="0.260001", unit="UF").amount_original, Decimal("0.260001"))

    def test_catalog_and_self_replacement_guards(self):
        for example in ({"source_type": "catalog"}, {"replaces_item_key": "rent"},
                        {"catalog_version_id": "not-a-uuid"}, {"id": DIAG}, {"catalog_snapshot": {}}):
            with self.subTest(example=example), self.assertRaises(ValidationError):
                item(**example)

    def test_assessment_capacity_contract(self):
        self.assertIsNone(FormalizationAssessmentInput(**assessment()).capacity)
        for example in (
            {"assessment_status": "partial", "capacity": "alta"},
            {"assessment_status": "complete"},
            {"assessment_status": "complete", "capacity": "alta", "missing_fields": ["tax"]},
        ):
            with self.subTest(example=example), self.assertRaises(ValidationError):
                FormalizationAssessmentInput(result={"reason": "test"}, **example)


class RpcTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.client.rpc.return_value.execute.return_value.data = context()
        self.deps = types.ModuleType("dependencies")
        self.deps.supabase_admin = self.client
        # Cualquier acceso a la conexión RAG o al cliente sin privilegios falla.
        def forbidden(name):
            raise AssertionError(f"El diagnóstico no debe consultar dependencies.{name}")
        self.deps.__getattr__ = forbidden
        modules = patch.dict(sys.modules, {"dependencies": self.deps})
        modules.start()
        self.addCleanup(modules.stop)

    def get(self, **kwargs):
        return db.get_formalization_context(USER, DIAG, as_of=DAY, **kwargs)

    def save(self, patch_value=None, **kwargs):
        return db.save_formalization_draft(USER, DIAG, 3, patch_value, as_of=DAY, **kwargs)

    def confirm(self, **kwargs):
        self.client.rpc.return_value.execute.return_value.data = {
            "diagnostic": context()["diagnostic"], "revision": {"id": REQUEST}, "replayed": False,
        }
        return db.confirm_formalization_revision(USER, DIAG, 3, REQUEST, assessment(), as_of=DAY, **kwargs)

    def test_get_uses_exactly_one_admin_rpc_without_reading_rag(self):
        self.assertEqual(self.get(), context())
        self.client.rpc.assert_called_once_with("get_formalization_context_v1", {
            "p_user_id": USER, "p_diagnostic_id": DIAG, "p_refresh_sources": False,
            "p_period_start": None, "p_period_end": None, "p_as_of": "2026-10-16",
        })
        self.client.rpc.return_value.execute.assert_called_once()
        self.client.table.assert_not_called()

    def test_new_profile_can_have_no_diagnostic(self):
        self.client.rpc.return_value.execute.return_value.data = {"diagnostic": None, "items": []}
        result = db.get_formalization_context(USER, as_of=DAY)
        self.assertIsNone(result["diagnostic"])
        self.assertIsNone(self.client.rpc.call_args.args[1]["p_diagnostic_id"])

    def test_one_element_response_wrapper_is_supported(self):
        self.client.rpc.return_value.execute.return_value.data = [context()]
        self.assertEqual(self.get(), context())

    def test_bad_context_never_becomes_an_empty_success(self):
        for bad in (None, [], [{}, {}], "ok", {}, {"items": []},
                    {"diagnostic": {}, "items": None}, {"diagnostic": {}, "items": [None]},
                    {"diagnostic": "bad", "items": []}, {**context(), "fresh_sources": None}):
            with self.subTest(bad=bad), self.assertRaises(db.FormalizationPersistenceError):
                self.client.rpc.return_value.execute.return_value.data = bad
                self.get()

    def test_refresh_remains_explicit_and_read_only(self):
        self.client.rpc.return_value.execute.return_value.data = {**context(), "fresh_sources": {"catalog": []}}
        self.get(refresh_sources=True, period_start=date(2026, 9, 1), period_end=date(2026, 10, 1))
        params = self.client.rpc.call_args.args[1]
        self.assertTrue(params["p_refresh_sources"])
        self.assertEqual(params["p_period_start"], "2026-09-01")
        self.assertEqual(self.client.rpc.call_count, 1)
        self.client.table.assert_not_called()

    def test_bad_or_implicit_period_fails_before_network(self):
        for values in ({"period_start": DAY}, {"period_end": DAY},
                       {"period_start": DAY, "period_end": DAY, "refresh_sources": True},
                       {"period_start": date(2020, 1, 1), "period_end": DAY, "refresh_sources": True},
                       {"period_start": date(2026, 9, 1), "period_end": date(2026, 10, 1)}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.get(**values)
        self.client.rpc.assert_not_called()

    def test_save_preserves_omitted_fields_and_explicit_null(self):
        self.save({"current_step": "expenses", "pending_field": None})
        name, params = self.client.rpc.call_args.args
        self.assertEqual(name, "save_formalization_draft_v1")
        self.assertEqual(params["p_patch"], {"current_step": "expenses", "pending_field": None})
        self.assertEqual(params["p_expected_lock_version"], 3)
        self.assertFalse(params["p_refresh_sources"])
        self.assertFalse(params["p_reopen"])
        self.assertEqual(self.client.rpc.call_count, 1)

    def test_save_serializes_batch_dates_and_decimal_without_float(self):
        self.save(FormalizationDraftPatch(period_start=date(2026, 9, 1), period_end=date(2026, 10, 1)),
                  items=[item(amount_original="0.260001", unit="UF", due_date=DAY)],
                  delete_item_keys=["old"], reopen=True)
        params = self.client.rpc.call_args.args[1]
        self.assertEqual(params["p_items"][0]["amount_original"], "0.260001")
        self.assertEqual(params["p_items"][0]["due_date"], "2026-10-16")
        self.assertEqual(params["p_delete_item_keys"], ["old"])
        self.assertTrue(params["p_reopen"])
        self.assertNotIn("catalog_snapshot", params["p_items"][0])
        self.assertEqual(self.client.rpc.call_count, 1)
        self.client.table.assert_not_called()

    def test_missing_saved_header_is_an_error(self):
        self.client.rpc.return_value.execute.return_value.data = {"diagnostic": None, "items": []}
        with self.assertRaises(db.FormalizationPersistenceError):
            self.save()

    def test_bad_version_is_rejected_before_request(self):
        for value in (-1, True, 1.5, "3", 2**63):
            with self.subTest(value=value), self.assertRaises(ValueError):
                db.save_formalization_draft(USER, DIAG, value)
        self.client.rpc.assert_not_called()

    def test_invalid_batch_is_rejected_before_request(self):
        for values in ({"items": [item(), item()]}, {"items": [item()] * 201},
                       {"delete_item_keys": ["x"] * 201}, {"delete_item_keys": ["x", "x"]},
                       {"delete_item_keys": [""]}, {"delete_item_keys": [None]},
                       {"items": [item()], "delete_item_keys": ["rent"]}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.save(**values)
        self.client.rpc.assert_not_called()

    def test_nested_non_finite_numbers_are_not_sent(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.save({"answers": {"income": value}})
        with self.assertRaises(ValueError):
            self.save(items=[item(evidence={"cost": math.nan})])
        self.client.rpc.assert_not_called()

    def test_invalid_uuid_fails_before_network(self):
        with self.assertRaises(ValueError):
            db.get_formalization_context("not-a-uuid")
        self.client.rpc.assert_not_called()

    def test_confirm_uses_one_rpc_and_keeps_server_revision(self):
        result = self.confirm()
        self.assertFalse(result["replayed"])
        self.assertEqual(result["revision"]["id"], REQUEST)
        name, params = self.client.rpc.call_args.args
        self.assertEqual(name, "confirm_formalization_revision_v1")
        self.assertEqual(params["p_request_id"], REQUEST)
        self.assertEqual(params["p_assessment"]["missing_fields"], [])
        self.assertEqual(self.client.rpc.call_count, 1)
        self.client.table.assert_not_called()

    def test_confirmation_replay_needs_no_second_read(self):
        expected = {"revision": {"snapshot": {"original": True}}, "replayed": True}
        self.client.rpc.return_value.execute.return_value.data = expected
        result = db.confirm_formalization_revision(USER, DIAG, 3, REQUEST, assessment(), as_of=DAY)
        self.assertEqual(result, expected)
        self.assertEqual(self.client.rpc.call_count, 1)

    def test_bad_confirmation_fails_closed(self):
        for bad in ({}, {"revision": {}, "replayed": "true"}, {"revision": None, "replayed": True},
                    {"revision": {}, "replayed": False}, {"revision": {}, "replayed": False, "diagnostic": None}):
            self.client.rpc.return_value.execute.return_value.data = bad
            with self.subTest(bad=bad), self.assertRaises(db.FormalizationPersistenceError):
                db.confirm_formalization_revision(USER, DIAG, 3, REQUEST, assessment(), as_of=DAY)

    def test_oversized_confirmation_is_not_sent(self):
        with self.assertRaises(ValueError):
            db.confirm_formalization_revision(USER, DIAG, 3, REQUEST,
                {"assessment_status": "partial", "result": {"text": "x" * 512000}}, as_of=DAY)
        self.client.rpc.assert_not_called()

    def test_database_conflicts_keep_cause_without_retry(self):
        for code in ("40001", "40P01", "23505"):
            original = RuntimeError("private database details")
            original.code = code
            self.client.rpc.return_value.execute.side_effect = original
            with self.subTest(code=code), self.assertRaises(db.FormalizationConflictError) as raised:
                self.get()
            self.assertIs(raised.exception.__cause__, original)
        self.assertEqual(self.client.rpc.call_count, 3)

    def test_not_found_keeps_cause(self):
        original = RuntimeError("private database details")
        original.code = "P0002"
        self.client.rpc.return_value.execute.side_effect = original
        with self.assertRaises(db.FormalizationNotFoundError) as raised:
            self.get()
        self.assertIs(raised.exception.__cause__, original)

    def test_other_api_failures_are_not_treated_as_empty_data(self):
        for code in ("42P01", "PGRST202", "42501", "23514"):
            original = RuntimeError("private database details")
            original.code = code
            self.client.rpc.return_value.execute.side_effect = original
            with self.subTest(code=code), self.assertRaises(RuntimeError) as raised:
                self.get()
            self.assertIs(raised.exception, original)

    def test_missing_admin_never_falls_back_to_other_client_or_rag(self):
        self.deps.supabase_admin = None
        with self.assertRaises(db.FormalizationConnectionError):
            self.get()
        self.client.rpc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
