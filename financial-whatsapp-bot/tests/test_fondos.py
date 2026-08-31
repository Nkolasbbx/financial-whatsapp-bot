import unittest
from datetime import date
from unittest.mock import patch

from core.fondos import evaluate_fund, evaluate_requirement, simulate_funds


def _definition(
    *,
    source_type="user_answer",
    profile_field=None,
    operator="equals",
    expected=True,
    question="¿Cumples este requisito?",
    question_order=10,
    **rule_values,
):
    rule = {"operator": operator, **rule_values}
    if operator == "equals":
        rule["expected"] = expected
    return {
        "source_type": source_type,
        "profile_field": profile_field,
        "evaluation_rule": rule,
        "question": question,
        "question_order": question_order,
        "answer_type": "boolean",
        "options": [],
    }


class FundEvaluationTests(unittest.TestCase):
    def test_profile_requirement_uses_current_user_value(self):
        requirement = {"clave": "sin_inicio_sii"}
        definitions = {
            "sin_inicio_sii": _definition(
                source_type="user_profile",
                profile_field="inicio_sii",
                expected="no",
                question=None,
            )
        }

        self.assertTrue(
            evaluate_requirement(
                requirement,
                {"inicio_sii": "no"},
                {},
                definitions,
            )
        )
        self.assertFalse(
            evaluate_requirement(
                requirement,
                {"inicio_sii": "si"},
                {},
                definitions,
            )
        )

    def test_missing_user_answer_remains_unknown(self):
        result = evaluate_requirement(
            {"clave": "mayor_edad"},
            {},
            {},
            {"mayor_edad": _definition()},
        )

        self.assertIsNone(result)

    def test_numeric_between_rule_accepts_only_configured_range(self):
        definitions = {
            "ventas_crece": _definition(
                operator="between",
                min=200,
                max=25000,
            )
        }
        requirement = {"clave": "ventas_crece"}

        self.assertTrue(
            evaluate_requirement(
                requirement,
                {},
                {"ventas_crece": 500},
                definitions,
            )
        )
        self.assertFalse(
            evaluate_requirement(
                requirement,
                {},
                {"ventas_crece": 26000},
                definitions,
            )
        )

    def test_evaluation_reports_blockers_and_orders_missing_questions(self):
        fund = {
            "nombre": "Fondo de prueba",
            "fecha_cierre": "2027-04-30",
            "requisitos": [
                {
                    "clave": "genero_femenino",
                    "texto": "Requisito excluyente",
                    "obligatorio": True,
                    "corregible": False,
                },
                {
                    "clave": "proyecto_negocio",
                    "texto": "Preparar proyecto",
                    "obligatorio": True,
                    "corregible": True,
                },
                {
                    "clave": "mayor_edad",
                    "texto": "Mayor de edad",
                    "obligatorio": True,
                    "corregible": False,
                },
            ],
        }
        definitions = {
            "genero_femenino": _definition(question_order=20),
            "proyecto_negocio": _definition(question_order=50),
            "mayor_edad": _definition(question_order=10),
        }

        result = evaluate_fund(
            fund,
            {},
            {"genero_femenino": False},
            definitions,
            today=date(2026, 8, 30),
        )

        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["unknown"], 2)
        self.assertEqual(len(result["blocking_failures"]), 1)
        self.assertEqual(
            [item["clave"] for item in result["missing_questions"]],
            ["mayor_edad", "proyecto_negocio"],
        )

    def test_custom_pioneras_rule_uses_user_industry(self):
        requirement = {"clave": "rubro_pioneras"}
        definitions = {
            "rubro_pioneras": _definition(
                source_type="computed",
                operator="custom",
                handler="rubro_pioneras",
                question=None,
            )
        }

        self.assertTrue(
            evaluate_requirement(
                requirement,
                {"rubro_raw": "Servicios de tecnología"},
                {},
                definitions,
            )
        )
        self.assertFalse(
            evaluate_requirement(
                requirement,
                {"rubro_raw": "Venta de alimentos"},
                {},
                definitions,
            )
        )

    @patch("core.fondos._get_fondos_from_supabase")
    @patch("core.fondos.get_fund_answers", return_value={"mayor_edad": True})
    @patch("core.fondos.get_requirement_definitions")
    def test_simulation_uses_persisted_answers(
        self,
        definitions_mock,
        _answers_mock,
        funds_mock,
    ):
        definitions_mock.return_value = {"mayor_edad": _definition()}
        funds_mock.return_value = [{
            "id": "fund-1",
            "nombre": "Capital Semilla Emprende",
            "emoji": "💰",
            "fecha_cierre": date(2099, 4, 30),
            "activo": True,
            "requisitos": [{
                "clave": "mayor_edad",
                "texto": "Mayor de edad",
                "obligatorio": True,
                "corregible": False,
            }],
        }]

        response = simulate_funds({
            "id": "user-1",
            "inicio_sii": "no",
            "rubro": "alimentos",
        })

        self.assertIn("Compatibilidad: *100%*", response)
        self.assertNotIn("Información pendiente", response)


if __name__ == "__main__":
    unittest.main()
