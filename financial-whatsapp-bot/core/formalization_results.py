"""Mismo resultado para WhatsApp y portal; sin escrituras ni IA."""
from core.formalization_engine import evaluate_diagnostic
from core import formalization_short_flow as brief


def calculate_assessment(context):
    assessment = evaluate_diagnostic(context)
    if brief.enabled(context):
        answers = context["diagnostic"]["answers"]
        assessment.result["short_budget"] = brief.totals(context)
        assessment.result["short_flow_version"] = brief.VERSION
        assessment.result.setdefault("assumptions", []).append(
            ("Escenario aceptado: " if answers.get("short_assumptions_accepted") else "Escenario NO aceptado: ")
            + answers.get("short_assumptions", ""))
    return assessment
