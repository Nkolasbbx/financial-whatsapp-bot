"""Explicaciones fieles a caja, fechas y reglas ya calculadas por el motor."""
import unittest
from core.formalization_presentation import capacity_breakdown, capacity_detail
from tests.test_formalization_engine import scenario


def result_for(**values):
    projection = scenario(**values)
    return {"economic_assessment": {"capacity": projection["capacity"]}, "projection": projection,
            "initial_remaining_min": values.get("initial", 500000), "initial_remaining_max": values.get("initial", 500000)}


class ExplanationTests(unittest.TestCase):
    def test_high_explanation_contains_actual_budget_reserve_and_stress_numbers(self):
        result = result_for()
        text = "\n".join(capacity_breakdown(result))
        self.assertIn("ALTA", text)
        self.assertIn("Cobros previstos: $3.600.000", text)
        self.assertIn("Pagos previstos: $3.000.000", text)
        self.assertIn("Diferencia del periodo: $600.000", text)
        self.assertIn("Reserva objetivo: $1.000.000", text)
        self.assertIn("tres meses: $60.000", text)
        self.assertIn("resistes ventas 15% menores", text)

    def test_low_cash_gap_identifies_amount_and_first_uncovered_date(self):
        text = "\n".join(capacity_breakdown(result_for(cash=400000)))
        self.assertIn("BAJA", text)
        self.assertIn("Al comenzar faltan $100.000", text)
        self.assertIn("25/10/2026", text)
        self.assertNotIn("el negocio consume dinero disponible", text)

    def test_low_negative_flow_explains_loss_even_when_cash_is_sufficient(self):
        text = "\n".join(capacity_breakdown(result_for(cash=5000000, initial=0, income=1000000, payments=1050000)))
        self.assertIn("los pagos superan los cobros en $150.000", text)
        self.assertNotIn("Al comenzar faltan", text)

    def test_medium_can_be_one_peso_below_reserve_without_a_payment_gap(self):
        text = "\n".join(capacity_breakdown(result_for(cash=1499999)))
        self.assertIn("MEDIA", text)
        self.assertIn("faltan $1 en el momento", text)
        self.assertNotIn("🔴 Hay pagos sin cobertura", text)

    def test_medium_break_even_distinguishes_loss_of_cash_from_uncovered_payment(self):
        text = "\n".join(capacity_breakdown(result_for(cash=5000000, initial=0, income=1000000, payments=1000000)))
        self.assertIn("Los cobros y pagos del periodo quedan iguales", text)
        self.assertIn("Con ventas 15% menores, los pagos del periodo superarían los cobros", text)
        self.assertNotIn("🟡 Con esa caída de ventas habría pagos sin cobertura", text)

    def test_unclassified_scenario_does_not_receive_a_detailed_capacity_claim(self):
        result = result_for(income=0, payments=0, initial=0)
        self.assertEqual(capacity_breakdown(result), [])

    def test_whatsapp_pagination_preserves_all_explanation_and_stays_within_limits(self):
        result = result_for(cash=400000, income=800000, payments=1000000)
        context = {"diagnostic": {"result": result}}
        collected = []
        for page in range(20):
            output = capacity_detail(context, lambda ctx, body, options: {"body": body, "options": options}, page=page)
            self.assertLessEqual(len(output["body"]), 1024)
            self.assertLessEqual(len(output["options"]), 3)
            collected.append(output["body"])
            if not any(action == "gwhy_" + str(page + 1) for action, _ in output["options"]):
                break
        else:
            self.fail("Las páginas no terminan")
        complete_text = " ".join(" ".join(collected).split())
        for block in capacity_breakdown(result):
            # Pagination inserts page titles, so verify values and clauses rather than full blocks across page boundaries.
            for line in block.splitlines():
                if line.startswith("•"):
                    self.assertIn(" ".join(line.split()), complete_text)


if __name__ == "__main__":
    unittest.main()
