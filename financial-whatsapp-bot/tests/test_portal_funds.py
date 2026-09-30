import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from db.fondos import UNKNOWN_ANSWER
from routers import portal_funds


async def _run_immediately(function, *args, **kwargs):
    return function(*args, **kwargs)


def _request():
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(redis=object())),
    )


DEFINITIONS = {
    "proyecto_negocio": {
        "field_key": "proyecto_negocio",
        "label": "Proyecto y pitch",
        "source_type": "user_answer",
        "answer_type": "boolean",
        "question": "¿Ya tienes listo tu pitch?",
        "options": [
            {"id": "yes", "title": "Sí", "value": True},
            {"id": "no", "title": "No", "value": False},
            {"id": "unknown", "title": "No lo sé", "value": None},
        ],
    },
    "ventas_crece": {
        "field_key": "ventas_crece",
        "label": "Ventas anuales",
        "source_type": "user_answer",
        "answer_type": "number",
        "evaluation_rule": {"operator": "between", "min": 200, "max": 25000, "unit": "UF"},
    },
    "inicio_sii": {
        "field_key": "inicio_sii",
        "source_type": "user_profile",
    },
}

EVALUATION = {
    "fund": {
        "id": "fund-1",
        "nombre": "Capital Pioneras",
        "emoji": "🌟",
        "link": "https://example.com",
        "monto_max": 3500000,
        "fecha_cierre": date(2027, 4, 30),
    },
    "requirements": [
        {"clave": "proyecto_negocio", "texto": "Pitch", "cumple": None},
        {
            "clave": "rubro_pioneras",
            "texto": "Rubro no tradicional",
            "cumple": False,
            "recomendacion": "Revisa otro fondo",
            "corregible": False,
        },
    ],
    "met": 0,
    "failed": 1,
    "unknown": 1,
    "total": 2,
    "percentage": 0,
    "days_remaining": 30,
    "is_open": True,
    "blocking_failures": [{"clave": "rubro_pioneras"}],
    "missing_questions": [],
}


class PortalFundsTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_session_is_rejected(self):
        with patch.object(
            portal_funds,
            "get_session_phone",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as context:
                await portal_funds._authenticated_user(_request(), None)

        self.assertEqual(context.exception.status_code, 401)

    def test_payload_serializes_evaluation_and_relevant_fields(self):
        with (
            patch.object(
                portal_funds,
                "evaluate_available_funds",
                return_value=[EVALUATION],
            ),
            patch.object(
                portal_funds,
                "get_requirement_definitions",
                return_value=DEFINITIONS,
            ),
            patch.object(
                portal_funds,
                "get_fund_answer_records",
                return_value={"proyecto_negocio": UNKNOWN_ANSWER},
            ),
        ):
            payload = portal_funds._build_funds_payload({"id": "user-1"})

        fund = payload["funds"][0]
        self.assertEqual(fund["closing_date"], "2027-04-30")
        self.assertTrue(fund["blocked"])
        self.assertEqual(
            [requirement["status"] for requirement in fund["requirements"]],
            ["unknown", "failed"],
        )
        self.assertTrue(fund["requirements"][1]["blocking"])

        # ventas_crece no lo usa ningún fondo visible; inicio_sii no es editable.
        self.assertEqual([field["key"] for field in payload["fields"]], ["proyecto_negocio"])
        self.assertEqual(payload["fields"][0]["selected"], "unknown")
        self.assertFalse(payload["fields"][0]["answered"])

    async def test_update_saves_parsed_answers_for_session_user(self):
        save_mock = Mock()
        with (
            patch.object(
                portal_funds,
                "_authenticated_user",
                new=AsyncMock(return_value={"id": "session-user"}),
            ),
            patch.object(portal_funds, "_require_csrf", new=AsyncMock()),
            patch.object(
                portal_funds,
                "get_requirement_definitions",
                return_value=DEFINITIONS,
            ),
            patch.object(portal_funds, "save_fund_answers", new=save_mock),
            patch.object(
                portal_funds,
                "_build_funds_payload",
                return_value={"funds": [], "fields": []},
            ),
            patch.object(
                portal_funds,
                "run_in_threadpool",
                new=_run_immediately,
            ),
        ):
            await portal_funds.update_answers(
                portal_funds.FundAnswersUpdateRequest(
                    answers={"proyecto_negocio": "yes", "ventas_crece": 350.0},
                ),
                _request(),
                financial_session="session-id",
                csrf_token="csrf",
            )

        save_mock.assert_called_once_with(
            "session-user",
            {"proyecto_negocio": True, "ventas_crece": 350},
        )

    async def test_update_rejects_invalid_or_non_editable_answers(self):
        save_mock = Mock()
        with (
            patch.object(
                portal_funds,
                "_authenticated_user",
                new=AsyncMock(return_value={"id": "session-user"}),
            ),
            patch.object(portal_funds, "_require_csrf", new=AsyncMock()),
            patch.object(
                portal_funds,
                "get_requirement_definitions",
                return_value=DEFINITIONS,
            ),
            patch.object(portal_funds, "save_fund_answers", new=save_mock),
            patch.object(
                portal_funds,
                "run_in_threadpool",
                new=_run_immediately,
            ),
        ):
            for answers in (
                {"inicio_sii": "yes"},
                {"proyecto_negocio": "tal vez"},
                {"ventas_crece": -5.0},
            ):
                with self.subTest(answers=answers):
                    with self.assertRaises(HTTPException) as context:
                        await portal_funds.update_answers(
                            portal_funds.FundAnswersUpdateRequest(answers=answers),
                            _request(),
                            financial_session="session-id",
                            csrf_token="csrf",
                        )
                    self.assertEqual(context.exception.status_code, 422)

        save_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
