import unittest
from unittest.mock import patch

from core.fund_flow import (
    FUND_UPDATE_DATA_ID,
    FUND_UPDATE_FIELD_PREFIX,
    _INVALID_ANSWER,
    _parse_numeric_answer,
    handle_fund_message,
    should_handle_fund_message,
    start_fund_flow,
)


class FundFlowTests(unittest.TestCase):
    def test_questions_are_not_captured_by_pending_boolean_requirement(self):
        session = {"pending_field_key": "proyecto_negocio"}
        definition = {"answer_type": "boolean"}
        for message in (
            "¿Cómo hago mi pitch?", "¿Cómo postular a fondos?",
            "No sé qué significa ese requisito", "¿Qué me falta para formalizar?",
        ):
            with self.subTest(message=message):
                self.assertFalse(should_handle_fund_message(
                    {"id": "user-1"}, message, session, definition,
                ))

    def test_short_answers_and_invalid_numeric_attempts_stay_in_flow(self):
        session = {"pending_field_key": "requirement"}
        for message in ("sí", "no", "no sé", "tal vez", "quizás"):
            with self.subTest(message=message):
                self.assertTrue(should_handle_fund_message(
                    {"id": "user-1"}, message, session, {"answer_type": "boolean"},
                ))
        for message in ("1500 UF", "-10", "1,2,3"):
            with self.subTest(message=message):
                self.assertTrue(should_handle_fund_message(
                    {"id": "user-1"}, message, session, {"answer_type": "number"},
                ))

    @patch("core.fund_flow.cancel_fund_session")
    @patch("core.fund_flow.start_fund_session")
    @patch("core.fund_flow.get_available_fund_candidates", return_value=[])
    def test_no_available_funds_closes_selection_session(
        self, _evaluate, _start, cancel,
    ):
        self.assertIn("No encontré fondos", start_fund_flow({"id": "user-1"}))
        cancel.assert_called_once_with("user-1")

    @patch("core.fund_flow.save_fund_answer")
    @patch("core.fund_flow.start_fund_session")
    def test_old_answer_button_does_not_open_or_modify_evaluation(self, start, save):
        for session in (None, {"status": "selecting", "pending_field_key": None}):
            result = handle_fund_message(
                {"id": "user-1"}, "fund_answer:yes", session, None,
            )
            self.assertIn("ya no está activa", result)
        save.assert_not_called()
        start.assert_not_called()

    @patch("core.fund_flow.get_active_fund_session")
    @patch("core.fund_flow.get_requirement_definitions")
    @patch("core.fund_flow._save_updated_data", return_value="actualizado")
    def test_supplied_context_is_reused(self, update, get_definitions, get_session):
        session = {"status": "collecting_data", "pending_field_key": "proyecto_negocio", "fondo_id": None}
        definition = {"source_type": "user_answer", "answer_type": "boolean"}
        result = handle_fund_message({"id": "user-1"}, "sí", session, definition)
        self.assertEqual(result, "actualizado")
        update.assert_called_once_with(
            {"id": "user-1"}, "sí", "proyecto_negocio", definition,
        )
        get_definitions.assert_not_called()
        get_session.assert_not_called()

    @patch("core.fund_flow.get_available_fund_candidates")
    @patch("core.fund_flow.start_fund_session")
    @patch("core.fund_flow.get_fund_answer_records", return_value={"mayor_edad": True})
    @patch("core.fund_flow.get_requirement_definitions")
    def test_resuming_fund_only_asks_unanswered_requirements(
        self, get_definitions, _get_answers, start, get_candidates,
    ):
        definitions = {
            key: {
                "source_type": "user_answer", "answer_type": "boolean",
                "evaluation_rule": {"operator": "equals", "expected": True},
                "question": question, "question_order": order,
            }
            for key, question, order in (
                ("mayor_edad", "¿Eres mayor de edad?", 10),
                ("proyecto_negocio", "¿Tienes tu pitch?", 20),
            )
        }
        get_definitions.return_value = definitions
        fund = {"requisitos": [{"clave": key} for key in definitions]}
        get_candidates.return_value = [fund]
        result = start_fund_flow({"id": "user-1"})
        self.assertIn("¿Tienes tu pitch?", result["body"])
        self.assertNotIn("¿Eres mayor de edad?", result["body"])
        self.assertEqual(start.call_args.kwargs["pending_field_key"], "proyecto_negocio")
        self.assertEqual(start.call_args.kwargs["status"], "selecting")

    @patch("core.fund_flow.get_fund_answer_records", return_value={})
    @patch("core.fund_flow.get_requirement_definitions", return_value={})
    @patch("core.fund_flow.get_available_fund_candidates", return_value=[{"id": "fund-1"}])
    @patch("core.fund_flow.evaluate_available_funds")
    @patch("core.fund_flow.start_fund_session")
    def test_start_flow_returns_interactive_fund_list_when_no_questions_remain(
        self,
        start_session_mock,
        evaluate_funds_mock,
        _get_candidates,
        _get_definitions,
        _get_records,
    ):
        evaluate_funds_mock.return_value = [{
            "fund": {
                "id": "fund-1",
                "slug": "capital_semilla_emprende",
                "nombre": "Capital Semilla Emprende",
                "emoji": "💰",
                "fecha_cierre": None,
            },
            "percentage": 40,
            "blocking_failures": [],
            "unknown": 2,
        }]

        result = start_fund_flow({"id": "user-1", "inicio_sii": "no"})

        self.assertEqual(result["type"], "list")
        self.assertEqual(result["options"][0][0], FUND_UPDATE_DATA_ID)
        self.assertEqual(
            result["options"][1][0],
            "fund_select:capital_semilla_emprende",
        )
        self.assertIn("Capital Semilla Emprende", result["body"])
        self.assertIn("Si quieres saber más", result["body"])
        start_session_mock.assert_called_once_with("user-1")

    def test_update_data_action_is_routed_as_fund_flow(self):
        self.assertTrue(
            should_handle_fund_message({"id": "user-1"}, FUND_UPDATE_DATA_ID)
        )

    @patch("core.fund_flow.start_fund_session")
    @patch("core.fund_flow.get_requirement_definitions")
    def test_update_data_lists_only_user_answers(
        self,
        get_definitions_mock,
        start_session_mock,
    ):
        get_definitions_mock.return_value = {
            "proyecto_negocio": {
                "field_key": "proyecto_negocio",
                "label": "Proyecto y pitch",
                "source_type": "user_answer",
            },
            "inicio_sii": {
                "field_key": "inicio_sii",
                "label": "Inicio SII",
                "source_type": "user_profile",
            },
        }

        result = handle_fund_message({"id": "user-1"}, FUND_UPDATE_DATA_ID)

        self.assertEqual(result["type"], "list")
        self.assertEqual(
            result["options"],
            [(f"{FUND_UPDATE_FIELD_PREFIX}proyecto_negocio", "Proyecto y pitch")],
        )
        start_session_mock.assert_called_once_with("user-1")

    @patch("core.fund_flow.update_fund_session")
    @patch("core.fund_flow.start_fund_session")
    @patch("core.fund_flow.get_requirement_definitions")
    def test_selecting_data_to_update_asks_the_requirement_again(
        self,
        get_definitions_mock,
        start_session_mock,
        update_session_mock,
    ):
        get_definitions_mock.return_value = {
            "proyecto_negocio": {
                "field_key": "proyecto_negocio",
                "label": "Proyecto y pitch",
                "source_type": "user_answer",
                "answer_type": "boolean",
                "question": "¿Ya tienes listo tu pitch?",
                "options": [
                    {"id": "yes", "title": "Sí", "value": True},
                    {"id": "no", "title": "No", "value": False},
                ],
            }
        }

        result = handle_fund_message(
            {"id": "user-1"},
            f"{FUND_UPDATE_FIELD_PREFIX}proyecto_negocio",
        )

        self.assertEqual(result["type"], "buttons")
        self.assertIn("¿Ya tienes listo tu pitch?", result["body"])
        start_session_mock.assert_called_once_with(
            "user-1",
            status="collecting_data",
            pending_field_key="proyecto_negocio",
        )
        update_session_mock.assert_not_called()

    @patch("core.fund_flow._advance_fund_preselection", return_value="fondos recalculados")
    @patch("core.fund_flow.finish_fund_session")
    @patch("core.fund_flow.save_fund_answer")
    @patch("core.fund_flow.get_requirement_definitions")
    @patch("core.fund_flow.get_active_fund_session")
    def test_updated_answer_replaces_fund_data_and_recalculates(
        self,
        get_session_mock,
        get_definitions_mock,
        save_answer_mock,
        finish_session_mock,
        fund_list_mock,
    ):
        get_session_mock.return_value = {
            "status": "collecting_data",
            "fondo_id": None,
            "pending_field_key": "proyecto_negocio",
        }
        get_definitions_mock.return_value = {
            "proyecto_negocio": {
                "field_key": "proyecto_negocio",
                "label": "Proyecto y pitch",
                "source_type": "user_answer",
                "answer_type": "boolean",
                "options": [
                    {"id": "yes", "title": "Sí", "value": True},
                    {"id": "no", "title": "No", "value": False},
                ],
            }
        }
        user = {"id": "user-1"}

        result = handle_fund_message(user, "fund_answer:yes")

        self.assertEqual(result, "fondos recalculados")
        save_answer_mock.assert_called_once_with(
            "user-1",
            "proyecto_negocio",
            True,
        )
        finish_session_mock.assert_not_called()
        self.assertIn("Actualicé", fund_list_mock.call_args.args[1])

    @patch("core.fund_flow.find_active_fund", return_value={"id": "fund-1"})
    def test_fund_name_is_routed_without_active_session_lookup(
        self,
        _find_fund_mock,
    ):
        user = {"id": "user-1"}

        self.assertTrue(
            should_handle_fund_message(user, "Capital Pioneras Emprende")
        )

    def test_numeric_parser_accepts_chilean_thousands_format(self):
        self.assertEqual(_parse_numeric_answer("1.500 UF"), 1500)
        self.assertEqual(_parse_numeric_answer("250,5 UF"), 250.5)
        self.assertEqual(_parse_numeric_answer("1.500,5 UF"), 1500.5)
        self.assertEqual(_parse_numeric_answer("1500.5"), 1500.5)
        self.assertEqual(_parse_numeric_answer("0"), 0)

    def test_numeric_parser_does_not_extract_numbers_from_questions(self):
        for message in (
            "¿Cómo declaro el F29?", "F29", "tengo 2 dudas", "-1",
            "1,2,3", "1.50.0", "9" * 400,
        ):
            with self.subTest(message=message):
                self.assertIs(_parse_numeric_answer(message), _INVALID_ANSWER)

    @patch("core.fund_flow._evaluate_selected_fund", return_value="resultado")
    @patch("core.fund_flow.update_fund_session")
    @patch("core.fund_flow.save_fund_answer")
    @patch("core.fund_flow.get_requirement_definitions")
    @patch("core.fund_flow.get_fund_by_id")
    @patch("core.fund_flow.get_active_fund_session")
    def test_pending_boolean_answer_is_saved_and_flow_continues(
        self,
        get_session_mock,
        get_fund_mock,
        get_definitions_mock,
        save_answer_mock,
        update_session_mock,
        evaluate_mock,
    ):
        get_session_mock.return_value = {
            "status": "collecting_data",
            "fondo_id": "fund-1",
            "pending_field_key": "mayor_edad",
        }
        fund = {"id": "fund-1", "nombre": "Capital Semilla Emprende"}
        get_fund_mock.return_value = fund
        get_definitions_mock.return_value = {
            "mayor_edad": {
                "answer_type": "boolean",
                "options": [
                    {"id": "yes", "title": "Sí", "value": True},
                    {"id": "no", "title": "No", "value": False},
                ],
            }
        }

        result = handle_fund_message(
            {"id": "user-1"},
            "fund_answer:yes",
        )

        self.assertEqual(result, "resultado")
        save_answer_mock.assert_called_once_with(
            "user-1",
            "mayor_edad",
            True,
        )
        update_session_mock.assert_called_once_with(
            "user-1",
            clear_pending_field=True,
        )
        evaluate_mock.assert_called_once()

    @patch("core.fund_flow._evaluate_selected_fund", return_value="siguiente")
    @patch("core.fund_flow.update_fund_session")
    @patch("core.fund_flow.save_fund_answer")
    @patch("core.fund_flow.get_requirement_definitions")
    @patch("core.fund_flow.get_fund_by_id", return_value={"id": "fund-1"})
    @patch("core.fund_flow.get_active_fund_session")
    def test_unknown_answer_is_persisted_without_repeating_forever(
        self,
        get_session_mock,
        _get_fund_mock,
        get_definitions_mock,
        save_answer_mock,
        _update_session_mock,
        _evaluate_mock,
    ):
        get_session_mock.return_value = {
            "status": "collecting_data",
            "fondo_id": "fund-1",
            "pending_field_key": "proyecto_negocio",
        }
        get_definitions_mock.return_value = {
            "proyecto_negocio": {
                "answer_type": "boolean",
                "options": [
                    {"id": "unknown", "title": "No lo sé", "value": None},
                ],
            }
        }

        result = handle_fund_message(
            {"id": "user-1"},
            "fund_answer:unknown",
        )

        self.assertEqual(result, "siguiente")
        save_answer_mock.assert_called_once_with(
            "user-1",
            "proyecto_negocio",
            None,
        )


if __name__ == "__main__":
    unittest.main()
