"""Portal privado con repositorio de diagnóstico simulado; no servicios remotos."""
import copy
import unittest
from datetime import date
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch, AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from core import formalization_flow as flow
from services import portal_diagnostic as service
from routers import portal_diagnostic as api, portal
from schemas.portal_diagnostic import DiagnosticAdjustments
from db.formalization_diagnostics import FormalizationConflictError, FormalizationNotFoundError
from tests import test_formalization_short_flow as short_tests


class PortalDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.flow = short_tests.ShortFlowTests()
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)
        self.flow.begin()
        self.flow.finish()
        self.repo = self.flow.repo
        for name, callback in (("get_formalization_context", self.repo.get),
                               ("save_formalization_draft", self.repo.save),
                               ("confirm_formalization_revision", self.repo.confirm),
                               ("today", lambda: date(2026, 10, 18))):
            p = patch.object(service, name, side_effect=callback)
            p.start(); self.addCleanup(p.stop)

    def payload(self, changes=(), *, reviewed=False):
        d = self.repo.data["diagnostic"]
        return DiagnosticAdjustments(diagnostic_id=d["id"], lock_version=d["lock_version"],
                                     request_id=uuid4(), changes=list(changes), coverage_confirmed=reviewed)

    def change(self, key="estimate:C12", **values):
        return {"key": key, "mode": "manual", "amount": 200000, "paid": 50000,
                "frequency": "once", "due_date": "2026-11-01", **values}

    def test_handoff_does_not_reopen_or_change_whatsapp_diagnosis(self):
        before = self.repo.get()
        self.assertEqual(self.flow.press("short_adjust"), "__DIAGNOSTIC_PANEL_LINK__")
        self.assertEqual(self.repo.get(), before)

    def test_view_minimizes_private_data_and_displays_costs(self):
        result = service.view(self.repo.get())
        self.assertTrue(result["available"])
        self.assertNotIn("answers", result)
        self.assertNotIn("profile_snapshot", result)
        self.assertNotIn("financial_snapshot", result)
        self.assertNotIn("user_id", result)

    def test_batch_changes_preserve_finances_and_recalculate_same_engine(self):
        original = self.repo.get()
        result = service.update(self.flow.user["id"], self.payload([
            self.change(), self.change("estimate:C24", amount=5000, paid=0, frequency="monthly", due_date=None, included=True)]))
        self.assertEqual(result["summary"]["initial_remaining_max"], 150040)
        self.assertEqual(self.repo.data["diagnostic"]["result"]["future_known_operating_expenses"], 200000)
        self.assertEqual(self.repo.data["diagnostic"]["financial_snapshot"], original["diagnostic"]["financial_snapshot"])
        before = [i for i in original["items"] if not i["item_key"].startswith("estimate:")]
        after = [i for i in self.repo.data["items"] if not i["item_key"].startswith("estimate:")]
        self.assertEqual(before, after)
        self.assertEqual(len(self.repo.revisions), 2)
        self.assertIn("$150.040", self.flow.send("mi diagnóstico")["body"])

    def test_unknown_amount_stays_pending_not_zero(self):
        result = service.update(self.flow.user["id"], self.payload([{"key": "estimate:C12", "mode": "pending"}]))
        item = next(i for i in result["items"] if i["key"] == "estimate:C12")
        self.assertEqual(item["mode"], "pending")
        self.assertIsNone(item["max"])

    def test_stale_version_or_other_diagnostic_never_overwrites(self):
        payload = self.payload([self.change()])
        self.repo.data["diagnostic"]["lock_version"] += 1
        before = self.repo.get()
        with self.assertRaises(FormalizationConflictError): service.update(self.flow.user["id"], payload)
        self.assertEqual(self.repo.get(), before)
        payload = self.payload([self.change()]); payload.diagnostic_id = uuid4()
        with self.assertRaises(FormalizationNotFoundError): service.update(self.flow.user["id"], payload)

    def test_cannot_edit_movements_or_arbitrary_catalog_keys(self):
        before = self.repo.get()
        with self.assertRaises(ValueError): service.update(self.flow.user["id"], self.payload([self.change("current_income")]))
        self.assertEqual(self.repo.get(), before)

    def test_incomplete_financial_review_cannot_be_confirmed_from_portal(self):
        self.repo.data["diagnostic"].update(workflow_status="draft", current_step="income")
        self.assertFalse(service.view(self.repo.get())["available"])
        with self.assertRaises(FormalizationConflictError): service.update(self.flow.user["id"], self.payload([self.change()]))

    def test_invalid_batch_is_not_partially_saved(self):
        before = self.repo.get()
        with self.assertRaises(ValueError):
            service.update(self.flow.user["id"], self.payload([self.change(), self.change("estimate:C16", due_date="2020-01-01")]))
        self.assertEqual(self.repo.get(), before)

    def test_annual_not_divided_by_twelve(self):
        result = service.update(self.flow.user["id"], self.payload([self.change("estimate:C16", amount=120000, paid=0, frequency="annual")]))
        self.assertEqual(self.repo.data["diagnostic"]["result"]["short_budget"]["annual"]["max"], 120000)
        self.assertEqual(self.repo.data["diagnostic"]["result"]["future_known_operating_expenses"], 200010)

    def test_recalculation_failure_keeps_saved_cost_and_can_resume(self):
        with patch.object(service, "confirm_formalization_revision", side_effect=RuntimeError("secret")):
            with self.assertRaises(service.RecalculationPending): service.update(self.flow.user["id"], self.payload([self.change()]))
        self.assertTrue(service.view(self.repo.get())["recalculation_pending"])
        result = service.update(self.flow.user["id"], self.payload())
        self.assertFalse(result["recalculation_pending"])
        self.assertEqual(result["summary"]["initial_remaining_max"], 150040)

    def test_schema_rejects_duplicates_extra_fields_and_noninteger_money(self):
        for changes in ([self.change(), self.change()], [self.change(amount=-1)], [self.change(amount=2.5)], [self.change(user_id="evil")]):
            with self.subTest(changes=changes), self.assertRaises(ValidationError): self.payload(changes)

    def fill_pending_costs(self):
        changes = []
        for item in service.view(self.repo.get())["items"]:
            if item["mode"] != "pending":
                continue
            # Real supplied budgets for every unresolved concept in this fixture.
            changes.append(self.change(item["key"], amount=1000, paid=0,
                                       frequency=item["frequency"] if item["frequency"] in {"once", "monthly", "annual"} else "once"))
        return changes

    def test_completing_panel_produces_full_economic_diagnosis_on_both_channels(self):
        result = service.update(self.flow.user["id"], self.payload(self.fill_pending_costs(), reviewed=True))
        self.assertEqual(result["economic_status"], "complete")
        self.assertEqual(result["capacity"], "alta")
        self.assertEqual(result["pending_requirements"], [])
        d = self.repo.data["diagnostic"]
        self.assertEqual(d["assessment_status"], "complete")
        self.assertEqual(d["missing_fields"], [])
        self.assertFalse(d["result"]["economic_assessment"]["legal_validation"])
        self.assertTrue(d["result"]["verification_pending"])
        response = self.flow.send("mi diagnóstico")
        self.assertIn("ALTA", response["body"])
        self.assertNotIn("Faltan costos o validaciones", response["body"])
        self.assertIn("resiste una baja de ventas", response["body"])

    def test_unknown_cost_still_blocks_even_with_review_checked(self):
        result = service.update(self.flow.user["id"], self.payload(reviewed=True))
        self.assertIsNone(result["capacity"])
        self.assertTrue(any("Completa el monto" in label for label in result["pending_requirements"]))

    def test_all_amounts_need_explicit_review_then_can_finalize_without_retyping(self):
        result = service.update(self.flow.user["id"], self.payload(self.fill_pending_costs()))
        self.assertIsNone(result["capacity"])
        self.assertEqual(result["pending_requirements"], ["Confirma al final del panel que terminaste de revisar los costos."])
        result = service.update(self.flow.user["id"], self.payload(reviewed=True))
        self.assertEqual(result["capacity"], "alta")

    def test_missing_financial_data_is_shown_instead_of_hidden_validation(self):
        self.repo.data["diagnostic"]["answers"]["preparation_confirmed"] = False
        result = service.update(self.flow.user["id"], self.payload(self.fill_pending_costs(), reviewed=True))
        self.assertIsNone(result["capacity"])
        self.assertEqual(result["pending_requirements"], ["Indica por WhatsApp los otros pagos antes de comenzar (0 si no tienes)."])

    def test_complete_diagnosis_can_be_low_or_medium_and_keeps_real_reasons(self):
        for income, cash, expected in ((200000, 1500000, "baja"), (310000, 310000, "media")):
            with self.subTest(expected=expected):
                context = copy.deepcopy(self.repo.get())
                # Change budget items and corresponding answer together, as WhatsApp does.
                self.repo.data["diagnostic"]["answers"].update(income_total=income, available_cash=cash)
                for item in self.repo.data["items"]:
                    if item["item_key"] == "current_income":
                        item.update(amount_clp_min=income, amount_clp_max=income)
                result = service.update(self.flow.user["id"], self.payload(self.fill_pending_costs(), reviewed=True))
                self.assertEqual(result["capacity"], expected)
                self.assertTrue(result["capacity_explanation"])
                self.repo.data = context


class PortalDiagnosticHTTPTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI(); app.state.redis = object(); app.include_router(api.router); app.include_router(portal.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        for module, name, replacement in ((api, "FORMALIZATION_DIAGNOSTICS_ENABLED", True),
            (api, "get_session_phone", AsyncMock(return_value="+56900000000")),
            (api, "get_user", lambda phone: {"id": "11111111-1111-4111-8111-111111111111"})):
            p = patch.object(module, name, replacement); p.start(); self.addCleanup(p.stop)

    def test_unauthenticated(self):
        with patch.object(api, "get_session_phone", AsyncMock(return_value=None)):
            self.assertEqual(self.client.get('/portal/api/diagnostic').status_code, 401)

    def test_missing_csrf_rejects_before_write(self):
        payload = {"diagnostic_id": str(uuid4()), "lock_version": 1, "request_id": str(uuid4()), "changes": []}
        with patch.object(api, "validate_csrf_token", AsyncMock(return_value=False)), patch.object(service, "update") as update:
            self.assertEqual(self.client.put('/portal/api/diagnostic', json=payload).status_code, 403)
            update.assert_not_called()

    def test_read_uses_session_identity_and_no_cache(self):
        with patch.object(api, "get_formalization_context", return_value={"diagnostic": None}) as get:
            result = self.client.get('/portal/api/diagnostic?user_id=attacker')
            self.assertEqual(result.status_code, 200)
            get.assert_called_once_with("11111111-1111-4111-8111-111111111111")
            self.assertEqual(result.headers["cache-control"], "no-store")

    def test_magic_link_redirect_is_allowlisted(self):
        with patch.object(portal, "redeem_access_token", AsyncMock(return_value="phone")), patch.object(portal, "create_session", AsyncMock(return_value="session")):
            for tab, expected in (("diagnostico", "/portal?tab=diagnostico"), ("https://evil.example", "/portal")):
                result = self.client.get('/portal/acceso', params={"token": "test", "tab": tab}, follow_redirects=False)
                self.assertEqual(result.headers["location"], expected)

    def test_panel_has_mobile_page_and_versioned_assets(self):
        with patch.object(portal, "get_session_phone", AsyncMock(return_value="phone")), patch.object(portal, "get_user", return_value={"id": "user"}), patch.object(portal, "get_or_create_csrf_token", AsyncMock(return_value="csrf")):
            page = self.client.get('/portal?tab=diagnostico').text
            self.assertIn('name="viewport"', page)
            self.assertIn('portal_diagnostic.js?v=', page)
            self.assertIn('Guardar y recalcular', page)
            self.assertIn('content="csrf"', page)
