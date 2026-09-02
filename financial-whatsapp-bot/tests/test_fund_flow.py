import unittest
from unittest.mock import patch

from core.fund_flow import (
    FUND_UPDATE_DATA_ID,
    FUND_UPDATE_FIELD_PREFIX,
    _parse_numeric_answer,
    handle_fund_message,
    should_handle_fund_message,
    start_fund_flow,
)


class FundFlowTests(unittest.TestCase):
    @patch("core.fund_flow.evaluate_available_funds")
    @patch("core.fund_flow.start_fund_session")
    def test_start_flow_returns_interactive_fund_list(
        self,
        start_session_mock,
        evaluate_funds_mock,
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
        start_session_mock.assert_called_once_with("user-1")
        update_session_mock.assert_called_once_with(
            "user-1",
            status="collecting_data",
            pending_field_key="proyecto_negocio",
        )

    @patch("core.fund_flow._fund_list_widget", return_value="fondos recalculados")
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
        finish_session_mock.assert_called_once_with("user-1")
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
