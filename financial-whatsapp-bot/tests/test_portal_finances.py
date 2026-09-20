import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

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
        self.assertNotIn("portal_calendar.js", body)
        messages_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
