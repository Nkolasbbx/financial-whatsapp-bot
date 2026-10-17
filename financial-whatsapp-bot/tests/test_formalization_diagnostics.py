"""Pruebas sin credenciales ni Supabase. SQL real se valida en entorno de pruebas."""

import json
import math
import sys
import types
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import UUID

from pydantic import ValidationError

from db import formalization_diagnostics as db
from schemas.formalization_diagnostics import (
    FormalizationAssessmentInput, FormalizationDraftPatch, FormalizationItemInput,
)


USER = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
DIAG = "33333333-3333-4333-8333-333333333333"
REQUEST = "44444444-4444-4444-8444-444444444444"
CATALOG = "55555555-5555-4555-8555-555555555555"
DAY = date(2026, 10, 16)


def diagnostic(**changes):
    return dict({
        "id": DIAG, "user_id": USER, "lock_version": 3, "workflow_status": "draft",
        "period_start": date(2026, 9, 1), "period_end": date(2026, 10, 1),
        "projection_start": date(2026, 11, 1), "activity_code": "alimentos_elaboracion",
        "comuna_code": "recoleta", "location_type": "vivienda",
        "answers": {}, "profile_snapshot": {}, "financial_snapshot": {},
        "rules_version": "test-v1", "rules_config": {}, "technical_status": "not_reviewed",
        "source_fingerprint": "a" * 64, "catalog_fingerprint": "b" * 64,
    }, **changes)


def item(**changes):
    return FormalizationItemInput.model_validate(dict({
        "item_key": "rent", "label": "Arriendo", "item_kind": "current_operating",
        "economic_nature": "expense", "cash_direction": "outflow", "source_type": "user",
    }, **changes))


def stored_item(key="rent", **changes):
    model = item(item_key=key)
    return dict({
        **model.model_dump(exclude={"replaces_item_key"}),
        "id": str(UUID(int=100 + len(key))), "diagnostic_id": DIAG,
        "catalog_snapshot": {}, "replaces_item_id": None,
    }, **changes)


def sources(**changes):
    return dict({
        "profile_snapshot": {"id": USER}, "financial_snapshot": {"loaded": True},
        "source_fingerprint": "a" * 64, "catalog_fingerprint": "b" * 64,
        "catalog": [], "source_checked_at": datetime(2026, 10, 16, tzinfo=timezone.utc),
    }, **changes)


@contextmanager
def transaction_with(cursor):
    yield cursor


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
            {"amount_original": "NaN", "unit": "UF"},
            {"amount_original": "Infinity", "unit": "UF"},
            {"amount_original": "0.1234567", "unit": "UF"},
            {"amount_original": "0.26"}, {"amount_state": "quoted"},
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
        self.assertIsNone(FormalizationAssessmentInput(
            assessment_status="partial", result={"reason": "missing_cost"}).capacity)
        for example in (
            {"assessment_status": "partial", "capacity": "alta"},
            {"assessment_status": "complete"},
            {"assessment_status": "complete", "capacity": "alta", "missing_fields": ["tax"]},
        ):
            with self.subTest(example=example), self.assertRaises(ValidationError):
                FormalizationAssessmentInput(result={"reason": "test"}, **example)


class HelperTests(unittest.TestCase):
    def test_hash_is_stable_and_json_is_finite(self):
        self.assertEqual(db._hash({"b": 2, "a": 1}), db._hash({"a": 1, "b": 2}))
        self.assertEqual(len(db._hash({})), 64)
        with self.assertRaises(ValueError):
            db._dumps({"income": math.nan})

    def test_period_checks(self):
        db._validate_period(None, None)
        db._validate_period(date(2026, 9, 1), date(2026, 10, 1))
        for start, end in ((DAY, None), (None, DAY), (DAY, DAY), (DAY, date(2020, 1, 1)),
                           (date(2020, 1, 1), DAY)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                db._validate_period(start, end)

    def test_new_item_id_stable_and_bound_to_diagnostic(self):
        a = db._prepare_items(DIAG, [], [item()], set(), {})[0]
        b = db._prepare_items(DIAG, [], [item()], set(), {})[0]
        c = db._prepare_items(OTHER, [], [item()], set(), {})[0]
        self.assertEqual(a["id"], b["id"])
        self.assertNotEqual(a["id"], c["id"])

    def test_replacements_order_target_first(self):
        rows = db._prepare_items(DIAG, [], [item(item_key="future", replaces_item_key="rent"), item()], set(), {})
        self.assertEqual([r["item_key"] for r in rows], ["rent", "future"])
        self.assertEqual(rows[1]["replaces_item_id"], rows[0]["id"])

    def test_long_replacement_chain_does_not_use_python_recursion(self):
        inputs = [item(item_key=f"line-{n}", replaces_item_key=f"line-{n+1}" if n < 1100 else None)
                  for n in range(1101)]
        rows = db._prepare_items(DIAG, [], inputs, set(), {})
        self.assertEqual(rows[0]["item_key"], "line-1100")
        self.assertEqual(rows[-1]["item_key"], "line-0")

    def test_missing_or_cyclic_replacements_rejected(self):
        for inputs in ([item(replaces_item_key="missing")],
                       [item(replaces_item_key="future"), item(item_key="future", replaces_item_key="rent")]):
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                db._prepare_items(DIAG, [], inputs, set(), {})

    def test_cannot_delete_target_that_is_still_referenced(self):
        target = stored_item("rent")
        future = stored_item("future", replaces_item_id=target["id"])
        with self.assertRaises(ValueError):
            db._prepare_items(DIAG, [target, future], [], {"rent"}, {})

    def test_catalog_snapshot_not_from_caller(self):
        concept = {"id": CATALOG, "active": True, "title": "Verified record"}
        output = db._prepare_items(DIAG, [], [item(catalog_version_id=CATALOG)], set(), {CATALOG: concept})
        self.assertEqual(output[0]["catalog_snapshot"], concept)
        with self.assertRaises(ValueError):
            db._prepare_items(DIAG, [], [item(catalog_version_id=CATALOG)], set(), {})

    def test_old_catalog_snapshot_preserved_for_same_version(self):
        existing = stored_item(catalog_version_id=CATALOG, catalog_snapshot={"version": 1})
        row = db._prepare_items(DIAG, [existing], [item(catalog_version_id=CATALOG)], set(), {})[0]
        self.assertEqual(row["catalog_snapshot"], {"version": 1})

    def test_complete_rejects_unknown_costs_and_empty_items(self):
        for records in ([], [stored_item()], [stored_item(applicability="applies")]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                db._validate_complete(diagnostic(), records, sources())

    def test_complete_allows_documented_quote_but_not_unreviewed_tariff(self):
        row = stored_item(applicability="applies", amount_state="quoted", amount_clp_min=100,
                          amount_clp_max=200, evidence={"quote": "test"}, catalog_version_id=CATALOG)
        source = sources(catalog=[{"id": CATALOG, "pricing_method": "quote", "review_status": "pending"}])
        db._validate_complete(diagnostic(), [row], source)
        source["catalog"][0]["pricing_method"] = "formula"
        with self.assertRaises(ValueError):
            db._validate_complete(diagnostic(), [row], source)
        with self.assertRaises(db.FormalizationConflictError):
            db._validate_complete(diagnostic(), [row], sources())


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.cursor = MagicMock()
        self.tx = patch.object(db, "_transaction", side_effect=lambda **kwargs: transaction_with(self.cursor))
        self.tx.start()
        self.addCleanup(self.tx.stop)

    def test_get_context_without_refresh_does_not_query_finances(self):
        self.cursor.fetchone.return_value = {**diagnostic(), "_items": []}
        with patch.object(db, "_load_sources") as load:
            result = db.get_formalization_context(USER, DIAG)
        load.assert_not_called()
        self.cursor.execute.assert_called_once()
        self.assertEqual(result["diagnostic"]["id"], DIAG)
        self.assertNotIn("_items", result["diagnostic"])

    def test_context_scope_and_missing_record(self):
        self.cursor.fetchone.return_value = None
        with self.assertRaises(db.FormalizationNotFoundError):
            db.get_formalization_context(USER, DIAG)
        self.assertEqual(self.cursor.execute.call_args.args[1], (USER, DIAG, DIAG))

    def test_refresh_is_returned_not_saved(self):
        self.cursor.fetchone.return_value = {**diagnostic(), "_items": []}
        with patch.object(db, "_load_sources", return_value=sources()) as load:
            result = db.get_formalization_context(USER, DIAG, refresh_sources=True, as_of=DAY)
        load.assert_called_once()
        self.assertIn("fresh_sources", result)
        self.assertEqual(self.cursor.execute.call_count, 1)

    def test_source_hash_changes_when_ledger_changes(self):
        raw = {"profile": {"id": USER}, "financial": {"income_total": 0},
               "catalog": [], "source_checked_at": "now", "movements_hash": "first"}
        self.cursor.fetchone.return_value = raw
        first = db._load_sources(self.cursor, USER, diagnostic(), DAY)
        raw["movements_hash"] = "changed-after-edit-or-delete"
        second = db._load_sources(self.cursor, USER, diagnostic(), DAY)
        self.assertNotEqual(first["source_fingerprint"], second["source_fingerprint"])
        self.assertEqual(first["catalog_fingerprint"], second["catalog_fingerprint"])
        self.assertNotIn("limit 3", db._SOURCES_SQL.lower())
        self.assertIn("aportes_capital", db._SOURCES_SQL)

    def test_source_failure_is_not_empty_movements(self):
        self.cursor.execute.side_effect = RuntimeError("connection failed")
        with self.assertRaises(RuntimeError):
            db._load_sources(self.cursor, USER, diagnostic(), DAY)

    def test_stale_draft_and_foreign_owner_rejected_before_write(self):
        for current in (diagnostic(lock_version=4), diagnostic(user_id=OTHER)):
            self.cursor.reset_mock()
            self.cursor.fetchone.return_value = {**current, "_items": []}
            with self.subTest(current=current), self.assertRaises(db.FormalizationPersistenceError):
                db.save_formalization_draft(USER, DIAG, 3, {"current_step": "expenses"}, as_of=DAY)
            self.assertEqual(self.cursor.execute.call_count, 1)

    def test_confirmed_requires_explicit_reopen(self):
        self.cursor.fetchone.return_value = {**diagnostic(workflow_status="confirmed"), "_items": []}
        with self.assertRaises(db.FormalizationConflictError):
            db.save_formalization_draft(USER, DIAG, 3, {}, as_of=DAY)

    def test_reopen_clears_current_result_but_does_not_delete_history(self):
        self.cursor.fetchone.return_value = diagnostic(workflow_status="confirmed")
        with patch.object(db, "_read_context", return_value={"diagnostic": diagnostic(workflow_status="confirmed"), "items": []}), \
             patch.object(db, "_update_header", return_value=diagnostic()) as update:
            db.save_formalization_draft(USER, DIAG, 3, {}, reopen=True, as_of=DAY)
        fields = update.call_args.args[3]
        self.assertIsNone(fields["result"])
        self.assertIsNone(fields["capacity"])
        self.assertEqual(fields["assessment_status"], "partial")
        self.assertNotIn("latest_revision_id", fields)

    def test_save_normal_reply_does_not_refresh_sources(self):
        self.cursor.fetchone.return_value = diagnostic()
        context = {"diagnostic": diagnostic(), "items": []}
        with patch.object(db, "_read_context", return_value=context), \
             patch.object(db, "_update_header", return_value=diagnostic(lock_version=4)) as update, \
             patch.object(db, "_load_sources") as load:
            result = db.save_formalization_draft(USER, DIAG, 3, {"current_step": "expenses"}, as_of=DAY)
        load.assert_not_called()
        self.assertEqual(update.call_count, 1)
        self.assertEqual(result["diagnostic"]["lock_version"], 4)

    def test_normal_reply_uses_two_sql_statements_not_catalog_or_ledger(self):
        self.cursor.fetchone.side_effect = [{**diagnostic(), "_items": []}, diagnostic(lock_version=4)]
        with patch.object(db, "_load_sources") as load:
            result = db.save_formalization_draft(USER, DIAG, 3, {"current_step": "expenses"}, as_of=DAY)
        self.assertEqual(self.cursor.execute.call_count, 2)
        self.assertEqual(result["diagnostic"]["lock_version"], 4)
        load.assert_not_called()

    def test_scope_change_refreshes_once(self):
        self.cursor.fetchone.return_value = diagnostic()
        with patch.object(db, "_read_context", return_value={"diagnostic": diagnostic(), "items": []}), \
             patch.object(db, "_update_header", return_value=diagnostic()) as update, \
             patch.object(db, "_load_sources", return_value=sources()) as load:
            db.save_formalization_draft(USER, DIAG, 3, {"location_type": "local"}, as_of=DAY)
        load.assert_called_once()
        self.assertEqual(update.call_count, 2)

    def test_bulk_items_use_one_parameterized_statement(self):
        rows = db._prepare_items(DIAG, [], [item(), item(item_key="transport")], set(), {})
        db._upsert_items(self.cursor, rows)
        self.cursor.execute.assert_called_once()
        sql, params = self.cursor.execute.call_args.args
        self.assertIn("jsonb_array_elements(%s::jsonb)", sql)
        self.assertEqual(len(json.loads(params[0])), 2)
        self.assertNotIn("Arriendo", sql)

    def test_confirm_checks_freshness_before_inserting(self):
        self.cursor.fetchone.return_value = None  # no previous request
        with patch.object(db, "_read_context", return_value={"diagnostic": diagnostic(), "items": []}), \
             patch.object(db, "_load_sources", return_value=sources(source_fingerprint="c" * 64)):
            with self.assertRaises(db.FormalizationConflictError):
                db.confirm_formalization_revision(USER, DIAG, 3, REQUEST,
                    {"assessment_status": "partial", "result": {"pending": True}}, as_of=DAY)
        self.assertEqual(self.cursor.execute.call_count, 1)

    def test_confirm_partial_inserts_revision_then_updates_only_derived_fields(self):
        self.cursor.fetchone.side_effect = [None, {"id": REQUEST, "revision_number": 1}]
        with patch.object(db, "_read_context", return_value={"diagnostic": diagnostic(), "items": []}), \
             patch.object(db, "_load_sources", return_value=sources()), \
             patch.object(db, "_update_header", return_value=diagnostic(workflow_status="confirmed")) as update:
            result = db.confirm_formalization_revision(USER, DIAG, 3, REQUEST,
                {"assessment_status": "partial", "result": {"pending": True}}, as_of=DAY)
        self.assertFalse(result["replayed"])
        self.assertEqual(result["revision"]["revision_number"], 1)
        self.assertNotIn("answers", update.call_args.args[3])
        self.assertNotIn("source_fingerprint", update.call_args.args[3])
        self.assertIn("INSERT INTO public.formalization_diagnostic_revisions", self.cursor.execute.call_args_list[1].args[0])

    def test_identical_confirmation_replays_before_freshness_checks(self):
        assessment = {"assessment_status": "partial", "result": {"pending": True},
                      "capacity": None, "missing_fields": []}
        request_hash = db._hash({"user_id": USER, "diagnostic_id": DIAG, "lock_version": 3, "assessment": assessment})
        self.cursor.fetchone.return_value = {"id": REQUEST, "request_hash": request_hash, "snapshot": {"old": True}}
        with patch.object(db, "_read_context", return_value={"diagnostic": diagnostic(lock_version=99), "items": []}), \
             patch.object(db, "_load_sources") as load:
            result = db.confirm_formalization_revision(USER, DIAG, 3, REQUEST, assessment, as_of=DAY)
        self.assertTrue(result["replayed"])
        load.assert_not_called()
        self.assertEqual(self.cursor.execute.call_count, 1)

    def test_confirmation_request_cannot_be_reused_for_changed_payload(self):
        self.cursor.fetchone.return_value = {"request_hash": "wrong"}
        with patch.object(db, "_read_context", return_value={"diagnostic": diagnostic(), "items": []}):
            with self.assertRaises(db.FormalizationConflictError):
                db.confirm_formalization_revision(USER, DIAG, 3, REQUEST,
                    {"assessment_status": "partial", "result": {"pending": True}}, as_of=DAY)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.pool = MagicMock()
        self.conn = self.pool.getconn.return_value
        self.conn.isolation_level, self.conn.readonly, self.conn.autocommit = 1, False, False
        self.cursor = self.conn.cursor.return_value.__enter__.return_value
        self.psycopg_extras = types.ModuleType("psycopg2.extras")
        self.psycopg_extras.RealDictCursor = object()
        self.modules = patch.dict(sys.modules, {"psycopg2": types.ModuleType("psycopg2"),
                                               "psycopg2.extras": self.psycopg_extras})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.pool_patch = patch.object(db, "_get_pool", return_value=self.pool)
        self.pool_patch.start()
        self.addCleanup(self.pool_patch.stop)

    def test_success_commits_and_restores_connection_settings(self):
        with db._transaction(readonly=True) as cursor:
            self.assertIs(cursor, self.cursor)
        self.conn.commit.assert_called_once()
        self.assertEqual(self.conn.set_session.call_args.kwargs,
                         {"isolation_level": 1, "readonly": False, "autocommit": False})
        self.pool.putconn.assert_called_once_with(self.conn, close=False)

    def test_server_defaults_are_restored_explicitly_not_left_readonly(self):
        self.conn.isolation_level, self.conn.readonly = None, None
        with db._transaction(readonly=True):
            pass
        self.assertEqual(self.conn.set_session.call_args.kwargs,
                         {"isolation_level": "DEFAULT", "readonly": "DEFAULT", "autocommit": False})

    def test_failure_rolls_back_and_returns_connection(self):
        with self.assertRaisesRegex(RuntimeError, "injected"):
            with db._transaction():
                raise RuntimeError("injected failure")
        self.conn.commit.assert_not_called()
        self.assertEqual(self.conn.rollback.call_count, 2)
        self.pool.putconn.assert_called_once()

    def test_serialization_failure_is_a_conflict_not_silent_retry(self):
        error = RuntimeError("serialization")
        error.pgcode = "40001"
        with self.assertRaises(db.FormalizationConflictError):
            with db._transaction():
                raise error

    def test_failed_cleanup_discards_connection(self):
        self.conn.set_session.side_effect = [None, RuntimeError("cleanup")]
        with db._transaction():
            pass
        self.pool.putconn.assert_called_once_with(self.conn, close=True)


if __name__ == "__main__":
    unittest.main()
