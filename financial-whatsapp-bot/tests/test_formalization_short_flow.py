"""Flujo breve real con repositorio simulado y botones obtenidos del render.

No se parchea la creación del diagnóstico ni el selector/motor de cálculo.
"""
import copy
import itertools
import unittest
from unittest.mock import patch

from core import formalization_flow as flow
from core import formalization_short_flow as brief
from core import formalization_cost_guide as guide
from tests import test_formalization_acceptance as pilot
from tests.test_formalization_estimates import real_catalog


class ShortFlowTests(unittest.TestCase):
    send = pilot.PilotAcceptanceTests.send
    press = pilot.PilotAcceptanceTests.press

    def setUp(self):
        pilot.PilotAcceptanceTests.setUp(self, detailed=False)

    def begin(self, location="local"):
        self.send("diagnóstico")
        self.press("start")
        self.press(location)
        self.press("yes")  # previous complete month
        self.press("yes")  # existing expenses
        self.press("none")  # no exceptional purchases
        self.press("yes")  # existing sales
        self.send("1500000")
        self.send("100000")
        self.send("0")
        self.press("yes")  # representative month
        self.press("yes")  # future starting month
        self.send("0")  # additional preparation
        self.press("yes")  # prudent payment calendar
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "short_needs")

    def finish(self, *, equipment=0, accept=True):
        for choice in (0, 1, equipment):
            self.press("short_choice_" + str(choice))
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "short_assumptions")
        return self.press("short_accept" if accept else "short_reject")

    def item(self, code):
        return next(i for i in self.repo.data["items"] if i["item_key"] == "estimate:" + code)

    def test_intro_precedes_first_question_and_can_pause_without_financial_answers(self):
        response = self.send("diagnóstico")
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "intro")
        self.assertIn("10 a 15 minutos", response["body"])
        self.assertIn("solo si aplican", response["body"])
        self.assertIn("no una verdad absoluta", response["body"])
        self.assertNotIn("¿Dónde funcionará", response["body"])
        self.assertNotIn("income_total", self.repo.data["diagnostic"]["answers"])
        self.press("pause")
        self.assertEqual(self.repo.data["diagnostic"]["conversation_status"], "paused")
        self.send("retomar diagnóstico")
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "intro")
        self.send("Comenzar")
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "location")

    def test_resume_existing_location_does_not_restart_intro(self):
        self.send("diagnóstico")
        old_token = next(token for token, _ in self.response["options"] if token.endswith(":start"))
        self.send(old_token)
        before = self.repo.get()
        self.assertIn("otra pregunta", self.send(old_token))
        self.assertEqual(self.repo.get(), before)
        self.send("mi diagnóstico")
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "location")

    def select(self, key):
        for _ in range(10):
            actions = [token.rsplit(":", 1)[-1] for token, _ in self.response["options"]]
            if "gcost_" + key in actions:
                return self.press("gcost_" + key)
            self.press("gnextpage")
        self.fail("Costo no accesible desde su grupo")

    def adjust(self, group, key):
        # Compatibility fixture: an edit already in progress before portal handoff.
        # New adjustments now return the secure portal-link marker without writes.
        self.assertEqual(self.press("short_adjust"), "__DIAGNOSTIC_PANEL_LINK__")
        context = flow._set_step(self.user, self.repo.get(), "short_groups")
        self.response = flow._prompt(context)
        self.press("short_group_" + group)
        self.select(key)

    def test_default_flow_has_three_business_questions_and_one_group_confirmation(self):
        self.begin()
        financial = copy.deepcopy(self.repo.data["diagnostic"]["financial_snapshot"])
        response = self.finish()
        self.assertTrue(brief.enabled(self.repo.data))
        self.assertEqual(self.repo.data["diagnostic"]["workflow_status"], "confirmed")
        self.assertEqual(len(self.repo.revisions), 1)
        self.assertEqual(len(self.repo.data["diagnostic"]["answers"]["short_answers"]), 3)
        self.assertEqual(self.repo.data["diagnostic"]["financial_snapshot"], financial)
        self.assertEqual(self.repo.refreshes, 2)
        result = self.repo.data["diagnostic"]["result"]
        self.assertEqual(result["current_income"], 500000)
        self.assertEqual(result["current_expenses"], 200000)
        self.assertEqual(result["initial_remaining_min"], 80)
        self.assertEqual(result["initial_remaining_max"], 140)
        self.assertIn("Trámites y permisos", response["body"])
        self.assertIn("Arreglos y equipos", response["body"])
        self.assertIn("pendientes", response["body"])
        self.assertIsNone(self.repo.data["diagnostic"]["capacity"])

    def test_owned_equipment_not_purchased_again(self):
        self.begin()
        self.finish(equipment=0)
        self.assertEqual(self.item("C13")["applicability"], "not_applicable")

    def test_pending_cost_notice_requests_user_amount_without_blocking(self):
        self.begin()
        self.finish()
        self.assertIn("debes completarlos en «Ajustar en mi panel»", self.response["body"])
        self.assertIn("fuera del total; no son gratuitos", self.response["body"])
        self.adjust("permits", "C16")
        self.assertIn("no podemos estimarlo con suficiente precisión", self.response["body"])
        self.assertIn("municipalidad, un profesional o proveedor", self.response["body"])
        self.assertIn("no significa que sea gratuito", self.response["body"])
        self.press("gskip")
        self.assertEqual(self.repo.data["diagnostic"]["workflow_status"], "confirmed")
        self.assertIsNone(self.item("C16")["amount_clp_max"])

    def test_saved_user_budget_is_not_described_as_excluded(self):
        self.begin()
        self.finish()
        self.adjust("permits", "C16")
        self.press("gmanual")
        self.send("120000")
        self.press("gcharge")
        self.send("0")
        self.press("gassume")
        self.adjust("permits", "C16")
        self.assertIn("Guardado: $120.000", self.response["body"])
        self.assertNotIn("quedará fuera del total", self.response["body"])
        self.assertEqual(self.item("C16")["amount_clp_max"], 120000)

    def test_missing_equipment_is_unknown_not_a_full_package_or_zero(self):
        self.begin()
        self.finish(equipment=1)
        self.assertEqual(self.item("C13")["applicability"], "to_confirm")
        self.assertIsNone(self.item("C13")["amount_clp_max"])
        self.assertGreater(self.repo.data["diagnostic"]["result"]["short_budget"]["setup"]["pending"], 0)

    def test_reject_assumptions_does_not_apply_hypothetical_prices(self):
        self.begin()
        self.finish(accept=False)
        self.assertEqual(self.repo.data["diagnostic"]["result"]["initial_remaining_max"], 0)
        self.assertEqual(self.item("C07")["applicability"], "to_confirm")
        self.assertIn("NO aceptado", self.repo.data["diagnostic"]["result"]["assumptions"][-1])

    def test_unknown_business_answers_do_not_block_or_fabricate_permits(self):
        self.begin()
        for _ in range(3):
            self.send("no sé")
        self.press("short_accept")
        self.assertIsNone(self.item("C07")["amount_clp_max"])
        self.assertIsNone(self.item("C13")["amount_clp_max"])

    def test_text_labels_work_as_well_as_buttons(self):
        self.begin()
        self.send("Preparo alimentos")
        self.send("Necesita arreglos")
        self.send("Tengo todos")
        self.send("Ver estimación")
        self.assertEqual(self.repo.data["diagnostic"]["workflow_status"], "confirmed")

    def test_question_pauses_and_resumes_without_consuming_business_answer(self):
        self.begin()
        before = copy.deepcopy(self.repo.data["diagnostic"]["answers"])
        self.assertEqual(self.send("¿Qué significa patente?"), flow.PASS_TO_AI)
        self.assertEqual(self.repo.data["diagnostic"]["answers"], before)
        self.send("mi diagnóstico")
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "short_needs")

    def test_foreign_button_pauses_without_swallowing_it(self):
        self.begin()
        result = flow.handle_diagnostic_message(self.user, "menu_roadmap", external_action=True)
        self.assertIsNone(result)
        self.assertEqual(self.repo.data["diagnostic"]["conversation_status"], "paused")

    def test_old_button_does_not_create_second_revision(self):
        self.begin()
        for choice in (0, 1, 0):
            self.press("short_choice_" + str(choice))
        token = next(t for t, _ in self.response["options"] if t.endswith(":short_accept"))
        self.send(token)
        self.assertIn("otra pregunta", self.send(token))
        self.assertEqual(len(self.repo.revisions), 1)

    def test_single_manual_cost_replaces_estimate_and_keeps_other_answers(self):
        self.begin()
        self.finish()
        original = copy.deepcopy(self.repo.data["diagnostic"]["answers"])
        self.adjust("setup", "C12")
        self.press("gmanual")
        self.send("200000")
        self.press("guser")
        self.send("50000")
        self.press("gassume")
        self.assertEqual(self.repo.data["diagnostic"]["workflow_status"], "confirmed")
        self.assertEqual(len(self.repo.revisions), 2)
        self.assertEqual(self.item("C12")["amount_paid_clp"], 50000)
        self.assertEqual(self.repo.data["diagnostic"]["result"]["short_budget"]["setup"]["max"], 150000)
        for key in ("income_total", "expenses_total", "short_answers"):
            self.assertEqual(self.repo.data["diagnostic"]["answers"][key], original[key])

    def test_incomplete_adjustment_preserves_previous_estimate(self):
        self.begin()
        self.finish()
        item = copy.deepcopy(self.item("C12"))
        self.adjust("setup", "C12")
        self.press("gmanual")
        self.send("900000")
        self.send("¿Dónde puedo cotizar?")
        self.assertEqual(self.item("C12"), item)
        self.send("retomar diagnóstico")
        self.send("Revisar después")
        self.assertEqual(self.item("C12"), item)

    def test_included_monthly_cost_not_added_twice(self):
        self.begin()
        self.finish()
        self.adjust("monthly", "C24")
        self.press("gaccept")
        self.press("yes")
        self.assertEqual(self.repo.data["diagnostic"]["result"]["future_known_operating_expenses"], 200000)
        self.assertEqual(self.repo.data["diagnostic"]["result"]["short_budget"]["monthly"]["max"], 0)

    def test_annual_cost_is_separate_from_monthly_costs(self):
        self.begin()
        self.finish()
        self.adjust("permits", "C16")
        self.press("gmanual")
        self.send("120000")
        self.press("guser")
        self.send("0")
        self.send("15/11/2026")
        groups = self.repo.data["diagnostic"]["result"]["short_budget"]
        self.assertEqual(groups["annual"]["max"], 120000)
        self.assertEqual(groups["monthly"]["max"], 10)

    def test_edit_income_recomputes_without_reasking_permits(self):
        self.begin()
        self.finish()
        costs = copy.deepcopy(guide.guide_items(self.repo.data))
        self.press("edit")
        self.press("edit_income")
        self.send("total 900000")
        self.press("yes")
        self.press("confirm")
        self.assertEqual(self.repo.data["diagnostic"]["result"]["current_income"], 900000)
        self.assertEqual(guide.guide_items(self.repo.data), costs)

    def test_sources_and_cost_details_are_accessible_and_paginated(self):
        self.begin()
        self.finish()
        self.press("details")
        self.press("sources")
        self.assertIn("test-v1", self.response["body"])
        self.press("gsummary")
        self.press("details")
        self.press("gdetails_0")
        for _ in range(100):
            actions = [t.rsplit(":", 1)[-1] for t, _ in self.response["options"]]
            page = int(self.response["body"].split("· ")[1].split("/")[0])
            if "gdetails_" + str(page) not in actions:
                break
            self.press("gdetails_" + str(page))
        else:
            self.fail("Paginación no termina")
        self.press("gsummary")

    def test_restarting_keeps_previous_revision_and_uses_short_default(self):
        self.begin()
        self.finish()
        old = self.repo.data["diagnostic"]["id"]
        self.send("nuevo diagnóstico")
        self.assertNotEqual(self.repo.data["diagnostic"]["id"], old)
        self.assertTrue(brief.enabled(self.repo.data))
        self.assertEqual(self.repo.closed_diagnostics[0]["diagnostic"]["workflow_status"], "archived")
        self.assertEqual(len(self.repo.revisions), 1)

    def test_out_of_scope_commune_does_not_receive_another_communes_prices(self):
        self.user["comuna"] = "Santiago"
        self.begin()
        self.finish()
        self.assertIsNone(self.item("C07")["amount_clp_max"])
        self.assertIsNone(self.item("C12")["amount_clp_max"])

    def test_excluded_estimate_recalculates_without_deleting_history(self):
        self.begin()
        self.finish()
        self.adjust("setup", "C12")
        self.press("gscope")
        self.press("gexclude")
        self.assertEqual(self.item("C12")["applicability"], "not_applicable")
        self.assertEqual(len(self.repo.revisions), 2)
        self.assertEqual(self.repo.revisions[0]["result"]["short_budget"]["setup"]["max"], 100)
        self.assertEqual(self.repo.revisions[1]["result"]["short_budget"]["setup"]["max"], 0)

    def test_current_month_explains_problem_without_losing_progress(self):
        self.send("diagnóstico")
        self.press("start")
        self.press("local")
        before = copy.deepcopy(self.repo.data)
        self.send("2026-10")
        self.assertIn("todavía no ha terminado", self.response["body"])
        self.assertEqual(self.repo.data, before)

    def test_confirm_failure_has_safe_retry_without_losing_costs(self):
        self.begin()
        for choice in (0, 1, 0):
            self.press("short_choice_" + str(choice))
        with patch.object(flow, "confirm_formalization_revision", side_effect=RuntimeError("private")):
            token = next(t for t, _ in self.response["options"] if t.endswith(":short_accept"))
            response = flow.handle_diagnostic_message(self.user, token)
        self.assertNotIn("private", str(response))
        self.assertEqual(self.repo.data["diagnostic"]["current_step"], "short_calculate")
        self.send("mi diagnóstico")
        self.press("confirm")
        self.assertEqual(len(self.repo.revisions), 1)

    def test_real_catalog_eight_pilot_profiles(self):
        self.check_matrix(real_catalog())

    def test_portable_eight_pilot_profiles(self):
        self.check_matrix(self.catalog["document"])

    def check_matrix(self, document):
        for activity, commune, location in itertools.product(("alimentos", "textil"), ("Recoleta", "El Bosque"), ("local", "vivienda")):
            with self.subTest(activity=activity, commune=commune, location=location):
                self.user.update(rubro=activity, comuna=commune)
                self.repo = pilot.PilotRepository(self.user)
                self.catalog["document"] = copy.deepcopy(document)
                self.begin(location)
                self.finish()
                self.assertEqual(self.repo.data["diagnostic"]["workflow_status"], "confirmed")
                self.assertEqual(self.repo.refreshes, 2)
                self.assertIsNone(self.repo.data["diagnostic"]["capacity"])


if __name__ == "__main__":
    unittest.main()
