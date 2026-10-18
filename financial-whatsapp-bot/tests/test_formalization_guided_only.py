"""No technical wizard, including existing drafts and previously issued buttons."""
import copy
import unittest
from unittest.mock import patch

from core import formalization_flow as flow
from tests import test_formalization_flow as helpers


class GuidedOnlyTests(unittest.TestCase):
    send = helpers.FlowTests.send
    button = helpers.FlowTests.button

    def setUp(self):
        helpers.FlowTests.setUp(self)
        self.send("diagnóstico")
        d = self.repo.data["diagnostic"]
        d["answers"] = {"expenses_total": 775000, "expenses_confirmed": True}
        d["current_step"] = "cost_amount"
        d["pending_field"] = "cost_amount"
        self.old_id = d["id"]

    def test_legacy_draft_does_not_render_technical_format_or_consume_answer(self):
        for message in ("retomar diagnóstico", "monto=150000; pagado=20000", "150000", flow._token(self.repo.data["diagnostic"], "yes")):
            with self.subTest(message=message):
                before = copy.deepcopy(self.repo.data)
                result = self.send(message)
                self.assertIn("preguntas simples", result["body"])
                self.assertNotIn("monto=", result["body"])
                self.assertEqual(self.repo.data, before)

    def test_explicit_restart_preserves_old_draft_and_starts_guided(self):
        self.send("retomar diagnóstico")
        self.button("guided_restart")
        d = self.repo.data["diagnostic"]
        self.assertNotEqual(d["id"], self.old_id)
        self.assertTrue(d["answers"]["estimate_catalog"])
        self.assertEqual(d["current_step"], "location")
        previous = self.repo.closed_diagnostics[0]["diagnostic"]
        self.assertEqual(previous["answers"]["expenses_total"], 775000)
        self.assertEqual(previous["workflow_status"], "cancelled")

    def test_missing_catalog_preserves_existing_data_without_legacy_prompt(self):
        before = copy.deepcopy(self.repo.data)
        with patch.object(flow, "_load_estimates", side_effect=flow.EstimateCatalogUnavailable):
            result = self.send("nuevo diagnóstico")
        self.assertIn("DIAG_CATALOG", result)
        self.assertNotIn("monto=", result)
        self.assertEqual(self.repo.data, before)

    def test_confirmed_legacy_is_readable_but_not_editable_in_technical_wizard(self):
        d = self.repo.data["diagnostic"]
        d.update(workflow_status="confirmed", conversation_status="closed", current_step="result",
                 result={"current_operating_result": 100000}, assessment_status="partial")
        before = copy.deepcopy(self.repo.data)
        self.assertIn("$100.000", self.send("mi diagnóstico")["body"])
        self.assertIn("preguntas simples", self.button("edit")["body"])
        self.assertEqual(self.repo.data, before)
        self.button("guided_restart")
        self.assertEqual(self.repo.closed_diagnostics[0]["diagnostic"]["workflow_status"], "archived")

    def test_retired_draft_does_not_block_menu_or_general_question(self):
        self.assertEqual(self.send("¿Cómo puedo vender más?"), flow.PASS_TO_AI)
        self.assertEqual(self.repo.data["diagnostic"]["conversation_status"], "paused")
        self.assertIsNone(self.send("menu_roadmap", external_action=True))


if __name__ == "__main__":
    unittest.main()
