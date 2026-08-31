import unittest
from datetime import date
from unittest.mock import patch

from core.fondos import (
    evaluate_available_funds,
    evaluate_fund,
    evaluate_requirement,
    format_fund_evaluation,
    format_funds_summary,
    get_requirement_urgency,
    simulate_funds,
)


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

    def test_sales_recommendation_changes_according_to_value(self):
        fund = {
            "nombre": "Crece",
            "fecha_cierre": "2027-05-31",
            "requisitos": [{
                "clave": "ventas_crece",
                "texto": "Ventas entre 200 y 25.000 UF",
                "obligatorio": True,
                "corregible": None,
            }],
        }
        definitions = {
            "ventas_crece": _definition(
                operator="between",
                min=200,
                max=25000,
            )
        }

        below = evaluate_fund(
            fund,
            {},
            {"ventas_crece": 100},
            definitions,
            today=date(2026, 8, 30),
        )
        above = evaluate_fund(
            fund,
            {},
            {"ventas_crece": 30000},
            definitions,
            today=date(2026, 8, 30),
        )

        self.assertTrue(below["requirements"][0]["corregible"])
        self.assertIn("bajo el mínimo", below["requirements"][0]["recomendacion"])
        self.assertFalse(above["requirements"][0]["corregible"])
        self.assertIn("superan el máximo", above["requirements"][0]["recomendacion"])
        self.assertEqual(len(above["blocking_failures"]), 1)

    def test_urgency_explains_reachability(self):
        reachable = get_requirement_urgency(
            {"plazo_dias": 14},
            days_remaining=20,
        )
        unreachable = get_requirement_urgency(
            {"plazo_dias": 28},
            days_remaining=10,
        )

        self.assertEqual(reachable["status"], "urgent")
        self.assertEqual(reachable["margin_days"], 6)
        self.assertEqual(unreachable["status"], "not_reachable")
        self.assertIn("No alcanzable", unreachable["label"])

    def test_detailed_result_orders_actions_by_available_margin(self):
        evaluation = {
            "fund": {
                "nombre": "Capital Semilla",
                "emoji": "💰",
                "fecha_cierre": date(2026, 9, 29),
            },
            "requirements": [
                {
                    "clave": "proyecto",
                    "texto": "Preparar pitch",
                    "cumple": False,
                    "corregible": True,
                    "plazo": "2 días",
                    "plazo_dias": 2,
                    "recomendacion": "Prepara el video.",
                },
                {
                    "clave": "capacitacion",
                    "texto": "Completar capacitación",
                    "cumple": False,
                    "corregible": True,
                    "plazo": "28 días",
                    "plazo_dias": 28,
                    "recomendacion": "Inscríbete al curso.",
                },
            ],
            "met": 0,
            "failed": 2,
            "unknown": 0,
            "total": 2,
            "percentage": 0,
            "days_remaining": 30,
            "is_open": True,
            "blocking_failures": [],
            "missing_questions": [],
        }

        message = format_fund_evaluation(evaluation, {})

        self.assertLess(
            message.index("Completar capacitación"),
            message.index("Preparar pitch"),
        )
        self.assertIn("Acciones recomendadas por urgencia", message)
        self.assertIn("Si quieres saber más", message)

    @patch("core.fondos._get_fondos_from_supabase")
    @patch("core.fondos.get_fund_answers", return_value={"requisito": True})
    @patch("core.fondos.get_requirement_definitions")
    def test_available_funds_are_grouped_and_sorted(
        self,
        definitions_mock,
        _answers_mock,
        funds_mock,
    ):
        definitions_mock.return_value = {"requisito": _definition()}
        funds_mock.return_value = [
            {
                "nombre": "Fondo bloqueado",
                "fecha_cierre": date(2027, 1, 1),
                "requisitos": [{
                    "clave": "requisito",
                    "texto": "Requisito",
                    "obligatorio": True,
                    "corregible": False,
                }],
            },
            {
                "nombre": "Fondo compatible",
                "fecha_cierre": date(2027, 2, 1),
                "requisitos": [{
                    "clave": "requisito",
                    "texto": "Requisito",
                    "obligatorio": True,
                    "corregible": False,
                }],
            },
        ]

        # Se cambia la respuesta entre evaluaciones mediante una regla por fondo
        # simulada: el primero queda bloqueado y el segundo cumplido.
        with patch(
            "core.fondos.evaluate_requirement",
            side_effect=[False, True],
        ):
            evaluations = evaluate_available_funds(
                {"id": "user-1", "inicio_sii": "no"},
                today=date(2026, 8, 30),
            )

        self.assertEqual(
            [item["fund"]["nombre"] for item in evaluations],
            ["Fondo compatible", "Fondo bloqueado"],
        )
        summary = format_funds_summary(evaluations)
        self.assertIn("Fondo compatible", summary)
        self.assertIn("Fondo bloqueado", summary)
        self.assertIn("escribe su nombre", summary)

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
