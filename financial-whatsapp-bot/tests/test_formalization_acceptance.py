"""Pilotos de HDU017 de punta a punta dentro del flujo, sin servicios externos.

Los botones se obtienen de los mensajes renderizados, incluida la paginación.
El repositorio es simulado: esto NO valida la migración ni el envío por Meta.
Los importes pequeños del catálogo sintético son datos de prueba, no tarifas.
"""
import copy
import itertools
import types
import unittest
from datetime import date
from unittest.mock import patch

from core import formalization_flow as flow
from core import formalization_cost_guide as guide
from db import formalization_diagnostics as diagnostic_db
from tests.test_formalization_flow import MemoryRepository, USER
from tests.test_formalization_cost_guide import catalog_row
from tests.test_formalization_estimates import real_catalog


class PilotRepository(MemoryRepository):
    def __init__(self, user):
        super().__init__()
        self.user = user
        self.empty = False
        self.writes = 0

    def save(self, *args, **kwargs):
        result = super().save(*args, **kwargs)
        self.writes += 1
        if result.get("fresh_sources"):
            d = self.data["diagnostic"]
            d["profile_snapshot"] = copy.deepcopy(self.user)
            if self.empty:
                d["financial_snapshot"] = {
                    "loaded": True, "expense_total": 0, "operating_income_total": 0,
                    "capital_contributions_total": 0, "other_income_total": 0, "categories": [],
                }
            result.update(self.get())
        return result


class PilotAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.user = copy.deepcopy(USER)
        self.repo = PilotRepository(self.user)
        self.catalog = catalog_row()
        self.response = None
        for name, callback in (
            ("get_formalization_context", lambda *a, **kw: self.repo.get(*a, **kw)),
            ("save_formalization_draft", lambda *a, **kw: self.repo.save(*a, **kw)),
            ("confirm_formalization_revision", lambda *a, **kw: self.repo.confirm(*a, **kw)),
            ("_load_estimates", lambda: copy.deepcopy(self.catalog)),
            ("_today", lambda: date(2026, 10, 18)),
        ):
            mock = patch.object(flow, name, side_effect=callback)
            mock.start()
            self.addCleanup(mock.stop)

    def send(self, message):
        self.response = flow.handle_diagnostic_message(self.user, message)
        if isinstance(self.response, dict):
            body = self.response["body"]
            self.assertNotIn("DIAG_", body, body)
            if self.response["type"] in {"buttons", "list"}:
                listing = self.response["type"] == "list"
                self.assertLessEqual(len(body), 1024, body)
                self.assertLessEqual(len(self.response["options"]), 10 if listing else 3)
                for token, label in self.response["options"]:
                    self.assertLessEqual(len(token), 200)
                    self.assertLessEqual(len(label), 24 if listing else 20, label)
        return self.response

    def press(self, action):
        options = dict((token.rsplit(":", 1)[-1], token) for token, _ in self.response["options"])
        self.assertIn(action, options, f"Botón {action} no está disponible: {self.response}")
        return self.send(options[action])

    def select(self, key):
        # Move through the actual list, never manufacture an invisible button.
        for _ in range(20):
            actions = [token.rsplit(":", 1)[-1] for token, _ in self.response["options"]]
            if "gcost_" + key in actions:
                return self.press("gcost_" + key)
            decisions = self.repo.data["diagnostic"]["answers"]["estimate_decisions"]
            target = next(i for i, row in enumerate(decisions) if row["key"] == key) // 7
            page = self.repo.data["diagnostic"]["answers"].get("estimate_page", 0)
            self.press("gnextpage" if target > page else "gprev")
        self.fail("La lista de costos no permite llegar al concepto")

    def begin(self, location="local", *, cash="1500000", owner="100000"):
        self.send("diagnóstico de formalización")
        self.press(location)
        self.press("yes")
        self.press("yes")
        self.press("none")
        self.press("yes")
        self.send(cash)
        self.send(owner)
        if self.repo.data["diagnostic"]["current_step"] == "confirm_owner":
            self.press("yes")
        self.send("0")
        self.press("yes")
        self.press("yes")
        self.send("0")
        self.press("yes")
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "estimate_needs")

    def needs(self, overrides=None):
        facts = {
            "subtype": "textil_confeccion" if self.user["rubro"] == "textil" else "alimentos_preparacion",
            "small_installation": True, "capital_under_10m": True,
            "mef_route": "accepted_hypothesis", "technical_review": "report",
            "missing_property_documents": False, "adaptations": "minor",
            "new_operating_costs_not_in_baseline": True,
            "municipal_patent_required": True, "waste_charge_required": False,
        }
        facts.update(overrides or {})
        for _ in range(40):
            q = guide.pending_question(self.repo.data)
            if q is None:
                break
            key, _, options = q
            if options:
                index = next(i for i, (value, _) in enumerate(options) if value == facts.get(key))
                self.press("gchoice_" + str(index))
            else:
                self.send(str(facts.get(key, 1)))
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "estimate_costs")

    def accept(self, key):
        self.select(key)
        self.press("gaccept")
        step = self.repo.data["diagnostic"]["current_step"]
        if step == "estimate_paid":
            self.send("0")
            self.press("gassume")
        elif step == "estimate_included":
            self.press("no")

    def finish(self, keep=()):
        # Explicit exclusions are only a hypothetical test scenario, not advice.
        for row in self.repo.data["diagnostic"]["answers"]["estimate_decisions"]:
            if row["key"] not in keep:
                self.select(row["key"])
                self.press("gscope")
                self.press("gexclude")
        self.press("gdone")
        self.press("yes")
        self.press("confirm")
        return self.repo.revisions[-1]["result"]

    def matrix(self):
        for activity, commune, location in itertools.product(
            ("alimentos", "textil"), ("Recoleta", "El Bosque"), ("local", "vivienda")
        ):
            with self.subTest(activity=activity, commune=commune, location=location):
                self.user.update(rubro=activity, comuna=commune)
                self.repo = PilotRepository(self.user)
                self.begin(location, cash="50000000")
                self.needs()
                decisions = {r["key"]: r for r in self.repo.data["diagnostic"]["answers"]["estimate_decisions"]}
                self.assertEqual(decisions["C12"]["variant_id"], ("al" if activity == "alimentos" else "tx") + "_" + location)
                sanitary = "C07" if activity == "alimentos" else "C08"
                kept = ("C01", sanitary, "C12", "C13", "C24")
                for key in kept:
                    self.accept(key)
                result = self.finish(kept)
                initial = [decisions[key] for key in kept if key != "C24"]
                self.assertEqual(result["initial_remaining_min"], sum(r["amount_clp_min"] for r in initial))
                self.assertEqual(result["initial_remaining_max"], sum(r["amount_clp_max"] for r in initial))
                self.assertEqual(result["current_operating_result"], 300000)
                self.assertEqual(result["future_known_operating_expenses"], 200000 + decisions["C24"]["amount_clp_max"])
                self.assertEqual(result["projection"]["opening_balance"], 50000000 - result["initial_remaining_max"])
                self.assertIsNotNone(result["economic_assessment"]["capacity"])
                self.assertIsNone(self.repo.revisions[-1]["capacity"])
                self.assertEqual(self.repo.refreshes, 2)  # create + chosen location, not every message

    def test_eight_pilot_combinations_with_portable_test_catalog(self):
        self.matrix()

    def test_eight_pilot_combinations_with_local_seed(self):
        self.catalog["document"] = real_catalog()
        self.catalog["version"] = self.catalog["document"]["version"]
        self.matrix()

    def test_zero_history_requires_confirmation_and_does_not_invent_capacity(self):
        self.repo.empty = True
        self.begin(owner="0")
        self.needs()
        result = self.finish()
        self.assertEqual(result["current_income"], 0)
        self.assertEqual(result["current_expenses"], 0)
        self.assertFalse(result["projection"]["has_operating_projection"])
        self.assertIsNone(result["economic_assessment"]["capacity"])
        self.assertIn("sin actividad", self.response["body"])
        self.assertIn("No tienes gastos registrados", flow._prompt({**self.repo.get(), "diagnostic": {**self.repo.data["diagnostic"], "current_step": "expenses"}})["body"])

    def test_partial_results_do_not_classify_and_can_be_read_without_writes(self):
        self.begin()
        self.needs()
        self.press("gdone")
        self.press("no")
        confirmation = next(t for t, _ in self.response["options"] if t.endswith(":confirm"))
        self.press("confirm")
        self.assertIsNone(self.repo.revisions[-1]["result"]["economic_assessment"]["capacity"])
        before = copy.deepcopy(self.repo.data)
        writes = self.repo.writes
        self.press("details")
        for _ in range(50):
            nxt = next((t.rsplit(":", 1)[-1] for t, label in self.response["options"] if label == "Siguiente"), None)
            if nxt is None:
                break
            self.press(nxt)
        self.press("gsummary")
        self.press("sources")
        self.assertEqual(self.repo.writes, writes)
        self.assertEqual(self.repo.data, before)
        self.assertIn("otra pregunta", self.send(confirmation))
        self.assertEqual(len(self.repo.revisions), 1)

    def test_edit_only_income_recalculates_without_losing_costs_or_previous_revision(self):
        self.begin()
        self.needs()
        self.accept("C12")
        self.finish(("C12",))
        previous = copy.deepcopy(self.repo.revisions[0])
        costs = copy.deepcopy(guide.guide_items(self.repo.data))
        self.press("edit")
        self.press("edit_income")
        self.press("income_details")
        self.send("800000")
        self.press("yes")
        self.press("yes")
        self.press("confirm")
        self.assertEqual(self.repo.revisions[0], previous)
        self.assertEqual(len(self.repo.revisions), 2)
        self.assertEqual(self.repo.revisions[-1]["result"]["current_operating_result"], 600000)
        self.assertEqual(guide.guide_items(self.repo.data), costs)
        self.assertEqual(self.repo.refreshes, 2)

    def test_capacity_categories_from_guided_answers(self):
        for cash, income, expected in ((1500000, 500000, "alta"), (350000, 300000, "media"), (0, 100000, "baja")):
            with self.subTest(expected=expected):
                self.repo = PilotRepository(self.user)
                self.begin(cash=str(cash))
                self.needs()
                self.finish()
                self.press("edit")
                self.press("edit_income")
                self.press("income_details")
                self.send(str(income))
                self.press("yes")
                self.press("yes")
                self.press("confirm")
                result = self.repo.revisions[-1]["result"]
                self.assertEqual(result["economic_assessment"]["capacity"], expected)
                self.assertFalse(result["economic_assessment"]["legal_validation"])
                self.press("details")
                self.assertIn("Por qué", self.response["body"])

    def test_actual_persistence_adapter_accepts_guided_payloads_with_existing_rpc(self):
        self.catalog["document"] = real_catalog()
        self.catalog["version"] = self.catalog["document"]["version"]
        def rpc(name, params):
            if name == "save_formalization_draft_v1":
                self.assertNotIn("estimate_catalog_version_id", params["p_patch"])
                return self.repo.save(params["p_user_id"], params["p_diagnostic_id"],
                    params["p_expected_lock_version"], params["p_patch"], items=params["p_items"],
                    delete_item_keys=params["p_delete_item_keys"], refresh_sources=params["p_refresh_sources"],
                    reopen=params["p_reopen"])
            self.assertEqual(name, "confirm_formalization_revision_v1")
            assessment = diagnostic_db.FormalizationAssessmentInput.model_validate(params["p_assessment"])
            result = self.repo.confirm(params["p_user_id"], params["p_diagnostic_id"],
                params["p_expected_lock_version"], params["p_request_id"], assessment)
            return {**result, "revision": {"id": params["p_request_id"]}, "replayed": False}
        with patch.object(diagnostic_db, "_rpc", side_effect=rpc), patch.object(
            flow, "save_formalization_draft", side_effect=diagnostic_db.save_formalization_draft
        ), patch.object(flow, "confirm_formalization_revision", side_effect=diagnostic_db.confirm_formalization_revision):
            self.begin()
            self.needs()
            self.accept("C12")
            result = self.finish(("C12",))
        self.assertEqual(result["estimate_version"], self.catalog["version"])
        self.assertEqual(len(self.repo.revisions), 1)

    def test_overlapping_document_costs_require_explicit_exclusion(self):
        self.catalog["document"] = real_catalog()
        self.catalog["version"] = self.catalog["document"]["version"]
        self.begin()
        self.needs()
        def quote(key, amount):
            self.select(key)
            self.press("gmanual")
            self.send(str(amount))
            self.press("gquote")
            self.send("0")
            return self.send("25/10/2026")
        quote("C09", 15000)
        before = copy.deepcopy(guide.guide_items(self.repo.data))
        response = quote("C10", 20000)
        self.assertIn("No lo necesito", response["body"])
        self.assertEqual(guide.guide_items(self.repo.data), before)
        self.press("gskip")
        self.select("C09")
        self.press("gscope")
        self.press("gexclude")
        quote("C10", 20000)
        result = self.finish(("C10",))
        self.assertEqual(result["initial_remaining_max"], 20000)


class CatalogLaunchTests(unittest.TestCase):
    def setUp(self):
        self.config = types.ModuleType("config")
        self.config.FORMALIZATION_ESTIMATE_PREVIEW_VERSION = ""
        patcher = patch.dict("sys.modules", {"config": self.config})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(flow, "_today", return_value=date(2026, 10, 18))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_default_reads_only_active_catalog_and_preview_is_explicit(self):
        with patch.object(flow, "get_formalization_estimate_catalog", return_value=catalog_row()) as read:
            flow._load_estimates()
            read.assert_called_once_with(version=None, include_inactive=False)
            self.config.FORMALIZATION_ESTIMATE_PREVIEW_VERSION = "test-v1"
            flow._load_estimates()
            read.assert_called_with(version="test-v1", include_inactive=True)

    def test_no_active_catalog_keeps_legacy_but_missing_preview_fails(self):
        with patch.object(flow, "get_formalization_estimate_catalog", return_value=None):
            self.assertIsNone(flow._load_estimates())
            self.config.FORMALIZATION_ESTIMATE_PREVIEW_VERSION = "missing"
            with self.assertRaises(ValueError):
                flow._load_estimates()

    def test_expired_catalog_is_not_used_for_a_new_diagnosis(self):
        row = catalog_row()
        row["review_by"] = "2026-10-17"
        with patch.object(flow, "get_formalization_estimate_catalog", return_value=row):
            with self.assertRaises(ValueError):
                flow._load_estimates()


if __name__ == "__main__":
    unittest.main()
