import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from routers import portal, portal_finances


async def _run_immediately(function, *args, **kwargs):
    return function(*args, **kwargs)


class PortalFinancesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(redis=object()),
            )
        )

    def test_month_range_is_semi_open_and_handles_december(self):
        self.assertEqual(
            portal_finances.month_range("2026-09"),
            (date(2026, 9, 1), date(2026, 10, 1)),
        )
        self.assertEqual(
            portal_finances.month_range("2026-12"),
            (date(2026, 12, 1), date(2027, 1, 1)),
        )

    def test_invalid_month_is_rejected(self):
        for value in ("09-2026", "2026-13", "texto"):
            with self.subTest(value=value):
                with self.assertRaises(HTTPException) as context:
                    portal_finances.month_range(value)
                self.assertEqual(context.exception.status_code, 422)

    async def test_missing_session_is_rejected(self):
        with patch.object(
            portal_finances,
            "get_session_phone",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as context:
                await portal_finances._authenticated_user(self.request, None)

        self.assertEqual(context.exception.status_code, 401)

    async def test_dashboard_uses_only_authenticated_user(self):
        summary_mock = Mock(return_value={
            "month_start": "2026-09-01",
            "month_end": "2026-10-01",
            "income_total": 100000,
            "expense_total": 35000,
            "net_total": 65000,
            "movement_count": 2,
            "income_categories": [{"category": "ventas", "total": 100000}],
            "expense_categories": [
                {"category": "insumos_mercaderia", "total": 35000}
            ],
        })
        movements_mock = Mock(return_value=[{
            "id": "movement-1",
            "user_id": "session-user",
            "movement_type": "income",
            "amount": 100000,
            "currency": "CLP",
            "category": "ventas",
            "description": "Venta de productos",
            "occurred_on": "2026-09-15",
            "original_text": "dato privado que no debe salir",
        }])

        with (
            patch.object(
                portal_finances,
                "_authenticated_user",
                new=AsyncMock(return_value={"id": "session-user"}),
            ),
            patch.object(
                portal_finances,
                "get_financial_month_summary",
                new=summary_mock,
            ),
            patch.object(
                portal_finances,
                "list_financial_movements",
                new=movements_mock,
            ),
            patch.object(
                portal_finances,
                "run_in_threadpool",
                new=_run_immediately,
            ),
        ):
            result = await portal_finances.financial_dashboard(
                self.request,
                month="2026-09",
                financial_session="session-id",
            )

        summary_mock.assert_called_once_with(
            "session-user",
            date(2026, 9, 1),
            date(2026, 10, 1),
        )
        self.assertEqual(movements_mock.call_args.args[0], "session-user")
        self.assertEqual(result.net_total, 65000)
        self.assertEqual(result.movements[0].description, "Venta de productos")
        self.assertFalse(hasattr(result.movements[0], "user_id"))
        self.assertFalse(hasattr(result.movements[0], "original_text"))

    async def test_finance_tab_does_not_load_chat_or_calendar_assets(self):
        with (
            patch.object(
                portal,
                "get_session_phone",
                new=AsyncMock(return_value="+56911111111"),
            ),
            patch.object(
                portal,
                "get_user",
                return_value={
                    "id": "session-user",
                    "phone": "+56911111111",
                    "roadmap": [],
                },
            ),
            patch.object(portal, "get_messages") as messages_mock,
            patch.object(portal, "get_or_create_csrf_token", AsyncMock(return_value="csrf-token")),
        ):
            response = await portal.panel(
                self.request,
                tab="finanzas",
                month="2026-09",
                financial_session="session-id",
            )

        body = response.body.decode("utf-8")
        self.assertIn("Resumen financiero", body)
        self.assertIn('aria-current="page">Finanzas</a>', body)
        self.assertIn("portal_finances.js", body)
        self.assertIn('name="financial-csrf-token" content="csrf-token"', body)
        self.assertIn('id="finance-form"', body)
        self.assertNotIn("portal_calendar.js", body)
        messages_mock.assert_not_called()


class PortalFinanceCreationTests(unittest.TestCase):
    def setUp(self):
        class FakeRedis:
            async def get(self, key):
                return {
                    "portal_session:valid-session": "+56911111111",
                    "portal_csrf:valid-session": "valid-csrf",
                }.get(key)

        app = FastAPI()
        app.state.redis = FakeRedis()
        app.include_router(portal_finances.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.client.cookies.set("financial_session", "valid-session")
        self.client.headers["X-CSRF-Token"] = "valid-csrf"
        self.payload = {
            "request_id": "fe94a354-57bb-42e0-9f8a-e84b3a926cfa",
            "movement_type": "income",
            "amount": 40000,
            "category": "ventas",
            "description": "  Venta de productos  ",
            "occurred_on": "2026-09-27",
        }
        self.create = Mock(side_effect=lambda user_id, request_id, kind, amount, category, description, occurred_on: {
            "id": "movement-1", "user_id": user_id, "movement_type": kind,
            "amount": amount, "category": category, "description": description,
            "occurred_on": occurred_on, "original_text": "private",
        })
        for target, value in (
            ("get_user", Mock(return_value={"id": "session-user"})),
            ("create_portal_financial_movement", self.create),
        ):
            patcher = patch.object(portal_finances, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def post(self, **changes):
        return self.client.post("/portal/api/finances/movements", json={**self.payload, **changes})

    def test_creates_income_for_session_user_and_returns_only_public_fields(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.text)
        self.create.assert_called_once_with(
            "session-user", self.payload["request_id"], "income", 40000,
            "ventas", "Venta de productos", date(2026, 9, 27),
        )
        self.assertNotIn("user_id", response.json())
        self.assertNotIn("original_text", response.json())

    def test_creates_expense(self):
        response = self.post(movement_type="expense", category="transporte", amount=12000)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["movement_type"], "expense")

    def test_missing_or_expired_session_cannot_write(self):
        for session in (None, "expired"):
            self.client.cookies.clear()
            if session:
                self.client.cookies.set("financial_session", session)
            self.assertEqual(self.post().status_code, 401)
        self.create.assert_not_called()

    def test_missing_or_invalid_csrf_cannot_write(self):
        del self.client.headers["X-CSRF-Token"]
        self.assertEqual(self.post().status_code, 403)
        self.client.headers["X-CSRF-Token"] = "wrong-token"
        self.assertEqual(self.post().status_code, 403)
        self.create.assert_not_called()

    def test_invalid_amounts_and_fields_cannot_write(self):
        invalid = [
            {"amount": 0}, {"amount": -1}, {"amount": 1.5}, {"amount": True},
            {"amount": "40000"}, {"amount": 9007199254740992},
            {"category": "transporte"}, {"movement_type": "other"},
            {"description": "   "}, {"description": "a" * 501},
            {"occurred_on": "2026-02-30"}, {"request_id": "invalid"},
            {"user_id": "another-user"}, {"target_movement_id": "another-movement"},
            {"original_text": "not permitted"},
        ]
        for changes in invalid:
            with self.subTest(changes=changes):
                response = self.post(**changes)
                self.assertEqual(response.status_code, 422, response.text)
        self.create.assert_not_called()

    def test_database_failure_returns_retryable_error_without_internal_details(self):
        self.create.side_effect = RuntimeError("private database details")
        with self.assertLogs("financial", level="ERROR"):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private database details", response.text)

    def test_reusing_request_with_different_data_returns_conflict(self):
        self.create.side_effect = ValueError("Solicitud ya utilizada")
        self.assertEqual(self.post().status_code, 409)


if __name__ == "__main__":
    unittest.main()
