"""Cuestionario compartido antes de elegir fondo, sin Supabase ni WhatsApp."""

import copy
import unittest
from datetime import date
from unittest.mock import patch

from core import fund_flow, fondos
from db.fondos import UNKNOWN_ANSWER


def definition(question, order, **overrides):
    return {
        "source_type": "user_answer", "answer_type": "boolean",
        "question": question, "question_order": order,
        "evaluation_rule": {"operator": "equals", "expected": True},
        "options": [
            {"id": "yes", "title": "Sí", "value": True},
            {"id": "no", "title": "No", "value": False},
            {"id": "unknown", "title": "No lo sé", "value": None},
        ],
        **overrides,
    }


class FundPreselectionRulesTests(unittest.TestCase):
    @patch("core.fondos.list_active_funds")
    def test_candidates_use_dates_and_sii_without_fallback(self, read):
        base = {"activo": True, "fecha_cierre": "2099-10-30", "requisitos": []}
        read.return_value = [
            {**base, "id": "open"},
            {**base, "id": "closing_today", "fecha_cierre": "2099-10-05"},
            {**base, "id": "closed", "fecha_cierre": "2099-10-04"},
            {**base, "id": "future", "fecha_apertura": "2099-10-06"},
            {**base, "id": "inactive", "activo": False},
            {**base, "id": "invalid", "fecha_cierre": "invalid"},
            {**base, "id": "undated", "fecha_cierre": None},
            {**base, "id": "formal", "requisitos": [{"clave": "inicio_sii"}]},
            {**base, "id": "informal", "requisitos": [{"clave": "sin_inicio_sii"}]},
        ]
        today = date(2099, 10, 5)
        result = fondos.get_available_fund_candidates({"inicio_sii": "no"}, today)
        self.assertEqual([fund["id"] for fund in result], ["open", "closing_today", "informal"])
        formal = fondos.get_available_fund_candidates({"inicio_sii": "si"}, today)
        self.assertEqual([fund["id"] for fund in formal], ["open", "closing_today", "formal"])
        read.return_value = []
        self.assertEqual(fondos.get_available_fund_candidates({}, today), [])

    @patch("core.fondos.list_active_funds", side_effect=RuntimeError("database unavailable"))
    def test_catalog_failure_is_not_replaced_by_example_funds(self, _read):
        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            fondos.get_available_fund_candidates({})

    def test_questions_are_unique_ordered_and_skip_profile_computed_and_saved(self):
        definitions = {
            "pitch": definition("¿Pitch?", 50),
            "edad": definition("¿Edad?", 10),
            "genero": definition("¿Género?", 20),
            "sii": definition("Perfil", 0, source_type="user_profile"),
            "rubro": definition("Cálculo", 0, source_type="computed"),
            "otro": definition("No pertenece a estos fondos", 1),
        }
        funds = [
            {"requisitos": [{"clave": key} for key in ("pitch", "edad", "sii", "rubro")]},
            {"requisitos": [{"clave": key} for key in ("pitch", "edad", "genero")]},
        ]
        questions = fondos.get_fund_preselection_questions(funds, definitions, {"genero"})
        self.assertEqual([question["field_key"] for question in questions], ["edad", "pitch"])

    @patch("core.fondos._get_fondos_from_supabase")
    def test_preloaded_empty_catalog_does_not_trigger_fallback(self, read):
        self.assertEqual(fondos.evaluate_available_funds(
            {}, definitions={}, answers={}, funds=[],
        ), [])
        read.assert_not_called()


class FundPreselectionFlowTests(unittest.TestCase):
    def setUp(self):
        self.user = {"id": "user-1", "phone": "+56911111111", "inicio_sii": "no"}
        self.definitions = {
            "mayor_edad": definition("¿Eres mayor de edad?", 10),
            "proyecto_negocio": definition("¿Ya tienes tu pitch?", 50),
        }
        self.funds = [
            {
                "id": "fund-1", "slug": "capital_semilla", "nombre": "Capital Semilla",
                "aliases": ["semilla"], "fecha_cierre": "2099-11-28",
                "requisitos": [
                    {"clave": "mayor_edad", "texto": "Mayor de edad"},
                    {"clave": "proyecto_negocio", "texto": "Pitch", "corregible": True},
                ],
            },
            {
                "id": "fund-2", "slug": "capital_abeja", "nombre": "Capital Abeja",
                "fecha_cierre": "2099-11-28",
                "requisitos": [{"clave": "proyecto_negocio", "texto": "Pitch"}],
            },
        ]
        self.records = {}
        self.session = None
        self.saved = []
        self.mocks = {}
        behaviors = {
            "get_available_fund_candidates": lambda _: copy.deepcopy(self.funds),
            "get_requirement_definitions": self._definitions,
            "get_fund_answer_records": lambda _: copy.deepcopy(self.records),
            "get_active_fund_session": lambda _: copy.deepcopy(self.session),
            "start_fund_session": self._start,
            "save_fund_answer": self._save,
            "finish_fund_session": self._finish,
            "cancel_fund_session": self._cancel,
        }
        for name, behavior in behaviors.items():
            patcher = patch.object(fund_flow, name, side_effect=behavior)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def _definitions(self, keys=None):
        return copy.deepcopy({
            key: value for key, value in self.definitions.items()
            if keys is None or key in keys
        })

    def _start(self, user_id, fund_id=None, *, status=None, pending_field_key=None):
        self.session = {
            "user_id": user_id, "fondo_id": fund_id, "pending_field_key": pending_field_key,
            "status": status or ("collecting_data" if fund_id else "selecting"),
        }
        return copy.deepcopy(self.session)

    def _save(self, user_id, key, value):
        self.records[key] = copy.deepcopy(UNKNOWN_ANSWER) if value is None else value
        self.saved.append((user_id, key, value))
        return {"field_key": key, "value": self.records[key]}

    def _finish(self, _user_id):
        self.session.update(status="evaluated", pending_field_key=None)

    def _cancel(self, _user_id):
        if self.session:
            self.session.update(status="cancelled", pending_field_key=None)

    def _reply(self, message):
        session = copy.deepcopy(self.session)
        key = session.get("pending_field_key") if session else None
        return fund_flow.handle_fund_message(
            self.user, message, session, self.definitions.get(key),
        )

    def test_questionnaire_finishes_before_fund_list_and_shared_pitch_is_asked_once(self):
        first = fund_flow.start_fund_flow(self.user)
        self.assertEqual(first["type"], "buttons")
        self.assertIn("¿Eres mayor de edad?", first["body"])
        self.assertEqual(self.session["status"], "selecting")
        self.assertIsNone(self.session["fondo_id"])
        second = self._reply("fund_answer:yes")
        self.assertIn("¿Ya tienes tu pitch?", second["body"])
        self.assertNotIn("Elegir fondo", second)
        result = self._reply("fund_answer:no")
        self.assertEqual(result["type"], "list")
        self.assertIn("Capital Semilla", result["body"])
        self.assertIn("Capital Abeja", result["body"])
        self.assertEqual(self.session["status"], "selecting")
        self.assertIsNone(self.session["pending_field_key"])
        self.assertEqual([key for _, key, _ in self.saved], ["mayor_edad", "proyecto_negocio"])

    def test_existing_answers_skip_questionnaire_and_selection_returns_detail(self):
        self.records = {"mayor_edad": True, "proyecto_negocio": False}
        result = fund_flow.start_fund_flow(self.user)
        self.assertEqual(result["type"], "list")
        detail = self._reply("fund_select:capital_semilla")
        self.assertIsInstance(detail, str)
        self.assertIn("Capital Semilla", detail)
        self.assertEqual(self.session["status"], "evaluated")
        self.assertEqual(self.session["fondo_id"], "fund-1")
        self.assertEqual(self.saved, [])

    def test_name_or_old_selection_button_cannot_skip_missing_answers(self):
        for message in ("Capital Semilla", "fund_select:capital_semilla"):
            with self.subTest(message=message):
                self.session = None
                with patch.object(fund_flow, "find_active_fund", return_value=self.funds[0]):
                    result = self._reply(message)
                self.assertEqual(result["type"], "buttons")
                self.assertIn("¿Eres mayor de edad?", result["body"])
                self.assertIsNone(self.session["fondo_id"])

    def test_selection_checks_questions_for_other_funds_too(self):
        self.funds[1]["requisitos"].append({"clave": "genero", "texto": "Sexo registral femenino"})
        self.definitions["genero"] = definition("¿Tu sexo registral es femenino?", 20)
        self.records = {"mayor_edad": True, "proyecto_negocio": True}
        result = self._reply("fund_select:capital_semilla")
        self.assertIn("¿Tu sexo registral es femenino?", result["body"])
        self.assertIsNone(self.session["fondo_id"])

    def test_unknown_answer_is_saved_and_not_repeated(self):
        fund_flow.start_fund_flow(self.user)
        result = self._reply("fund_answer:unknown")
        self.assertEqual(self.records["mayor_edad"], UNKNOWN_ANSWER)
        self.assertIn("¿Ya tienes tu pitch?", result["body"])
        result = self._reply("fund_answer:yes")
        self.assertEqual(result["type"], "list")
        detail = self._reply("fund_select:capital_semilla")
        self.assertIsInstance(detail, str)
        self.assertEqual(self.session["status"], "evaluated")

    def test_default_buttons_work_when_definition_has_no_options(self):
        self.definitions["mayor_edad"]["options"] = []
        fund_flow.start_fund_flow(self.user)
        result = self._reply("fund_answer:yes")
        self.assertTrue(self.records["mayor_edad"])
        self.assertIn("¿Ya tienes tu pitch?", result["body"])

    def test_resume_keeps_previous_answer_and_asks_only_pending_data(self):
        fund_flow.start_fund_flow(self.user)
        self._reply("sí")
        self._reply("cancelar evaluación")
        result = fund_flow.start_fund_flow(self.user)
        self.assertIn("¿Ya tienes tu pitch?", result["body"])
        self.assertNotIn("¿Eres mayor de edad?", result["body"])
        self.assertTrue(self.records["mayor_edad"])

    def test_editing_one_answer_is_distinct_from_initial_questionnaire(self):
        self.records = {"mayor_edad": True, "proyecto_negocio": False}
        result = self._reply("fund_update_field:proyecto_negocio")
        self.assertEqual(self.session["status"], "collecting_data")
        self.assertIn("Actualizarás", result["body"])
        result = self._reply("fund_answer:yes")
        self.assertEqual(result["type"], "list")
        self.assertIn("Actualicé", result["body"])
        self.assertEqual(self.records, {"mayor_edad": True, "proyecto_negocio": True})
        self.assertEqual(len(self.saved), 1)

    def test_no_funds_does_not_create_questionnaire_or_load_answers(self):
        self.funds = []
        result = fund_flow.start_fund_flow(self.user)
        self.assertIn("No encontré fondos", result)
        self.mocks["start_fund_session"].assert_not_called()
        self.mocks["get_requirement_definitions"].assert_not_called()
        self.mocks["get_fund_answer_records"].assert_not_called()
        self.assertEqual(self.saved, [])

    def test_invalid_numeric_answer_repeats_question_without_saving(self):
        self.definitions = {"ventas": definition(
            "¿Ventas en UF?", 10, answer_type="number", options=[],
            evaluation_rule={"operator": "between", "min": 200, "max": 25000, "unit": "UF"},
        )}
        self.funds = [{**self.funds[0], "requisitos": [{"clave": "ventas", "texto": "Ventas"}]}]
        fund_flow.start_fund_flow(self.user)
        result = self._reply("-10")
        self.assertIn("No pude interpretar", result)
        self.assertEqual(self.session["pending_field_key"], "ventas")
        self.assertEqual(self.saved, [])
        result = self._reply("1.500 UF")
        self.assertEqual(result["type"], "list")
        self.assertEqual(self.records["ventas"], 1500)

    def test_questionnaire_stays_nonblocking_and_resumes_through_message_router(self):
        from services import message_router

        user = {**self.user, "onboarding_step": "done", "reminder_count": 0}
        patches = []
        for name, kwargs in {
            "get_user": {"return_value": user},
            "get_active_fund_session": {"side_effect": lambda _: (
                copy.deepcopy(self.session)
                if self.session and self.session["status"] in {"selecting", "collecting_data"}
                else None
            )},
            "get_requirement_definitions": {"side_effect": self._definitions},
            "cancel_fund_session": {"side_effect": self._cancel},
            "get_calendar_session": {"return_value": None},
            "get_financial_session": {"return_value": None},
            "clear_calendar_session": {"return_value": None},
            "clear_financial_session": {"return_value": None},
        }.items():
            patcher = patch.object(message_router, name, **kwargs)
            patcher.start()
            patches.append(patcher)
        try:
            first = message_router.route_message(user["phone"], "postular fondos")
            self.assertIn("¿Eres mayor de edad?", first["body"])
            second = message_router.route_message(user["phone"], "fund_answer:yes")
            self.assertIn("¿Ya tienes tu pitch?", second["body"])
            answer = message_router.route_message(user["phone"], "¿Cómo declaro el F29?")
            self.assertEqual(answer, "__AI_QUERY__")
            self.assertEqual(self.session["status"], "cancelled")
            self.assertEqual(self.records, {"mayor_edad": True})
            resumed = message_router.route_message(user["phone"], "postular fondos")
            self.assertIn("¿Ya tienes tu pitch?", resumed["body"])
            final = message_router.route_message(user["phone"], "fund_answer:no")
            self.assertEqual(final["type"], "list")
        finally:
            for patcher in reversed(patches):
                patcher.stop()


if __name__ == "__main__":
    unittest.main()
