"""Flujo completo con repositorio en memoria; no envía WhatsApp ni usa SQL."""

import copy
import unittest
from unittest.mock import patch
from uuid import UUID, uuid5
from datetime import date

from core import formalization_flow as flow
from schemas.formalization_diagnostics import FormalizationDraftPatch, FormalizationItemInput


USER = {"id": "11111111-1111-4111-8111-111111111111", "rubro": "alimentos", "comuna": "Recoleta", "inicio_sii": "no"}
CATALOG = [{"id": "22222222-2222-4222-8222-222222222222", "code": "C01", "version": 1,
            "title": "Inicio ante SII", "description": "Trámite directo", "cost_nature": "permit",
            "frequency": "once", "mandatory_status": "conditional", "pricing_method": "fixed",
            "unit": "CLP", "amount_min": "0.000000", "amount_max": "0.000000", "review_status": "reviewed"}]


class MemoryRepository:
    def __init__(self):
        self.data = {"diagnostic": None, "items": []}
        self.revisions = []
        self.refreshes = 0

    def get(self, *args, **kwargs):
        return copy.deepcopy(self.data)

    def save(self, user_id, diagnostic_id, expected, patch, *, items=None, delete_item_keys=None, refresh_sources=False, reopen=False):
        values = FormalizationDraftPatch.model_validate(patch).model_dump(exclude_unset=True, mode="json")
        created = self.data["diagnostic"] is None
        if created:
            self.data["diagnostic"] = {"id": diagnostic_id, "user_id": user_id, "workflow_status": "draft",
                                       "answers": {}, "lock_version": 0, "conversation_status": "paused"}
        d = self.data["diagnostic"]
        if d["lock_version"] != expected:
            raise flow.FormalizationConflictError("stale")
        if d["workflow_status"] == "confirmed":
            assert reopen
            d.update(workflow_status="draft", result=None, capacity=None)
        refresh = created or refresh_sources or any(key in values and values[key] != d.get(key)
                    for key in ("period_start", "period_end", "location_type", "activity_code", "comuna_code"))
        d.update(values)
        if refresh:
            self.refreshes += 1
            d["profile_snapshot"] = copy.deepcopy(USER)
            d["financial_snapshot"] = {"loaded": True, "expense_total": 200000, "operating_income_total": 500000,
                "capital_contributions_total": 100000, "other_income_total": 50000,
                "categories": [{"movement_type": "expense", "category": "insumos_mercaderia", "total": 200000}]}
        existing = {i["item_key"]: i for i in self.data["items"] if i["item_key"] not in (delete_item_keys or [])}
        for raw in items or []:
            item = FormalizationItemInput.model_validate(raw).model_dump(mode="json")
            item["id"] = str(uuid5(UUID(diagnostic_id), item["item_key"]))
            cat = next((row for row in CATALOG if row["id"] == item["catalog_version_id"]), {})
            item["catalog_snapshot"] = copy.deepcopy(cat)
            if item["replaces_item_key"]:
                item["replaces_item_id"] = str(uuid5(UUID(diagnostic_id), item["replaces_item_key"]))
            existing[item["item_key"]] = item
        self.data["items"] = list(existing.values())
        d["lock_version"] += 1 + len(items or [])
        result = self.get()
        if refresh:
            result["fresh_sources"] = {"catalog": copy.deepcopy(CATALOG)}
        return result

    def confirm(self, user_id, diag_id, expected, request_id, assessment):
        d = self.data["diagnostic"]
        assert d["lock_version"] == expected
        self.revisions.append(copy.deepcopy(assessment.model_dump()))
        d.update(assessment.model_dump(), workflow_status="confirmed", conversation_status="closed", current_step="result")
        d["lock_version"] += 1
        return self.get()


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        for name, replacement in (("get_formalization_context", self.repo.get),
                                  ("save_formalization_draft", self.repo.save),
                                  ("confirm_formalization_revision", self.repo.confirm),
                                  ("_today", lambda: date(2026, 10, 16))):
            p = patch.object(flow, name, side_effect=replacement)
            p.start()
            self.addCleanup(p.stop)

    def send(self, text, **kwargs):
        result = flow.handle_diagnostic_message(USER, text, **kwargs)
        if isinstance(result, dict) and result["type"] in {"buttons", "list"}:
            self.assertLessEqual(len(result["body"]), 1024)
            self.assertLessEqual(len(result["options"]), 10 if result["type"] == "list" else 3)
            for _, label in result["options"]:
                self.assertLessEqual(len(label), 24 if result["type"] == "list" else 20)
        return result

    def button(self, action):
        return self.send(flow._token(self.repo.data["diagnostic"], action))

    def start(self):
        self.send("diagnóstico de formalización")
        self.button("local")
        self.button("yes")

    def to_costs(self):
        self.start()
        for text in ("sí", "sí", "1500000", "100000", "0", "sí", "2026-11", "0", "sí"):
            self.send(text)
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "costs")

    def test_full_partial_flow_persists_and_separates_money(self):
        self.to_costs()
        self.button("cost_C01")
        self.button("yes")
        self.button("costs_done")
        result = self.button("confirm")
        self.assertIn("Parcial", result["body"])
        self.assertIsNone(self.repo.data["diagnostic"]["capacity"])
        self.assertEqual(len(self.repo.revisions), 1)
        self.assertEqual(self.repo.revisions[0]["result"]["current_income"], 500000)
        self.assertEqual(self.repo.revisions[0]["result"]["initial_remaining_max"], 0)

    def test_profile_reused_and_sources_not_refetched_for_each_answer(self):
        self.to_costs()
        self.assertEqual(self.repo.refreshes, 2)  # creation + location
        self.assertEqual(self.repo.data["diagnostic"]["activity_code"], "alimentos")
        self.assertEqual(self.repo.data["diagnostic"]["comuna_code"], "recoleta")

    def test_unrelated_question_pauses_without_changing_financial_answers(self):
        self.start()
        before = copy.deepcopy(self.repo.data["diagnostic"]["answers"])
        self.assertEqual(self.send("¿Qué me falta para formalizar?"), flow.PASS_TO_AI)
        self.assertEqual(self.repo.data["diagnostic"]["conversation_status"], "paused")
        self.assertEqual(self.repo.data["diagnostic"]["answers"], before)
        self.assertIsNone(self.send("sí"))
        self.send("retomar diagnóstico")
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "expenses")

    def test_other_modules_buttons_pause_and_are_not_swallowed(self):
        for message in ("menu_roadmap", "menu_panel_web", "menu_calendar", "menu_fondo", "hito_listo", "finance_confirm"):
            self.send("retomar diagnóstico")
            self.assertIsNone(self.send(message, external_action=True))
            self.assertEqual(self.repo.data["diagnostic"]["conversation_status"], "paused")

    def test_reset_cancels_editable_diagnosis(self):
        self.start()
        self.assertIsNone(self.send("menu_reiniciar", external_action=True))
        self.assertEqual(self.repo.data["diagnostic"]["workflow_status"], "cancelled")

    def test_old_button_cannot_answer_a_new_question(self):
        self.start()
        button = flow._token(self.repo.data["diagnostic"], "yes")
        self.send("sí")
        before = copy.deepcopy(self.repo.data)
        self.assertIn("otra pregunta", self.send(button))
        self.assertEqual(self.repo.data, before)

    def test_manual_expenses_replace_observed_total_not_added_to_it(self):
        self.start()
        self.send("arriendo=100000; insumos=50000; servicios=20000; transporte=10000; otros=5000")
        self.assertEqual(self.repo.data["diagnostic"]["answers"]["expenses_total"], 185000)
        self.assertEqual(self.repo.data["diagnostic"]["financial_snapshot"]["expense_total"], 200000)

    def test_only_missing_income_added_to_recorded_sales(self):
        self.start()
        self.send("sí")
        self.send("$50.000")
        self.assertEqual(self.repo.data["diagnostic"]["answers"]["income_total"], 550000)

    def test_malformed_amount_does_not_advance_or_store_zero(self):
        self.start()
        self.send("sí")
        self.send("sí")
        self.send("1.2.3")
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "cash")
        self.assertNotIn("available_cash", self.repo.data["diagnostic"]["answers"])

    def test_changed_period_invalidates_period_answers_and_reloads_once(self):
        self.to_costs()
        context = self.repo.data
        self.repo.save(USER["id"], context["diagnostic"]["id"], context["diagnostic"]["lock_version"], {"current_step": "period"})
        count = self.repo.refreshes
        self.send("2026-08")
        self.assertEqual(self.repo.refreshes, count + 1)
        self.assertNotIn("income_confirmed", self.repo.data["diagnostic"]["answers"])
        self.assertFalse(any(i["item_key"] == "current_income" for i in self.repo.data["items"]))

    def test_reopen_changes_only_selected_values_and_preserves_revision(self):
        self.to_costs()
        self.button("costs_done")
        self.button("confirm")
        original = copy.deepcopy(self.repo.data["diagnostic"]["answers"])
        self.send("rehacer diagnóstico")
        self.button("edit_cash")
        self.send("2000000")
        updated = self.repo.data["diagnostic"]["answers"]
        self.assertEqual(updated["income_total"], original["income_total"])
        self.assertEqual(updated["expenses_total"], original["expenses_total"])
        self.assertEqual(updated["available_cash"], 2000000)
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "review")
        self.button("confirm")
        self.assertEqual(len(self.repo.revisions), 2)

    def test_catalog_long_list_is_paginated_under_meta_limit(self):
        self.to_costs()
        template = copy.deepcopy(self.repo.data["items"][0])
        self.repo.data["items"] = [{**template, "item_key": f"catalog:C{i:02}"} for i in range(1, 14)]
        self.send("retomar diagnóstico")
        self.button("next_costs")
        self.button("previous_costs")
        self.assertEqual(self.repo.data["diagnostic"]["answers"]["cost_page"], 0)

    def test_no_context_does_not_consume_general_queries(self):
        self.assertIsNone(self.send("¿Cómo vendo más?"))

    def test_explicit_database_failure_is_not_reported_as_empty_history(self):
        with patch.object(flow, "get_formalization_context", side_effect=RuntimeError("unavailable")), self.assertLogs("financial", level="ERROR"):
            self.assertIn("No pude consultar", self.send("mi diagnóstico"))

    def test_reset_also_closes_paused_and_confirmed_diagnoses(self):
        self.start()
        self.button("pause")
        self.send("menu_reiniciar", external_action=True)
        self.assertEqual(self.repo.data["diagnostic"]["workflow_status"], "cancelled")
        self.repo.data = {"diagnostic": None, "items": []}
        self.to_costs()
        self.button("costs_done")
        self.button("confirm")
        self.send("menu_reiniciar", external_action=True)
        self.assertEqual(self.repo.data["diagnostic"]["workflow_status"], "archived")
        self.assertEqual(len(self.repo.revisions), 1)

    def test_income_edit_keeps_expenses_but_reconfirms_projection(self):
        self.to_costs()
        self.button("costs_done")
        self.button("confirm")
        self.send("rehacer diagnóstico")
        self.button("edit_income")
        self.send("total 800000")
        self.assertEqual(self.repo.data["diagnostic"]["answers"]["expenses_total"], 200000)
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "representative")
        self.send("sí")
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "review")

    def test_cost_details_keep_confirmed_quote_without_claiming_tax_validation(self):
        self.to_costs()
        item = self.repo.data["items"][0]
        item["catalog_snapshot"].update(pricing_method="formula", review_status="pending")
        self.button("cost_C01")
        self.button("yes")
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "cost_amount")
        self.send("monto=50000; pagado=10000; fecha=2026-10-25; frecuencia=unico; incluido=no; respaldo=https://proveedor.cl/CotizacionABC")
        item = next(i for i in self.repo.data["items"] if i["item_key"] == "catalog:C01")
        self.assertEqual(item["amount_clp_max"], 50000)
        self.assertEqual(item["amount_paid_clp"], 10000)
        self.assertTrue(item["evidence"])
        self.assertIn("CotizacionABC", item["evidence"]["description"])

    def test_unknown_cost_is_not_saved_as_zero(self):
        self.to_costs()
        item = self.repo.data["items"][0]
        item["catalog_snapshot"].update(pricing_method="formula", review_status="pending")
        self.button("cost_C01")
        self.button("yes")
        self.button("unknown")
        item = self.repo.data["items"][0]
        self.assertIsNone(item["amount_clp_min"])
        self.assertEqual(item["amount_state"], "pending")

    def test_refresh_is_explicit_and_preserves_quotes_when_catalog_unchanged(self):
        self.to_costs()
        self.button("costs_done")
        self.button("confirm")
        self.send("rehacer diagnóstico")
        self.button("edit_refresh")
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "expenses")
        self.assertNotIn("income_confirmed", self.repo.data["diagnostic"]["answers"])


if __name__ == "__main__":
    unittest.main()
