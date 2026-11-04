import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from routers import reminders


class ReminderRouterTests(unittest.IsolatedAsyncioTestCase):
    @patch.object(reminders, "CRON_SECRET", "secret-for-tests")
    @patch.object(reminders, "send_due_community_notices", new_callable=AsyncMock)
    @patch.object(reminders, "send_due_calendar_reminders", new_callable=AsyncMock)
    @patch.object(reminders, "send_tax_alerts", new_callable=AsyncMock)
    @patch.object(reminders, "send_due_reminders", new_callable=AsyncMock)
    async def test_valid_bearer_executes_all_reminders(
        self, send_mock, alerts_mock, calendar_mock, community_mock
    ):
        send_mock.return_value = {"status": "completed", "sent": 1}
        alerts_mock.return_value = {"tax_alerts_sent": 2}
        calendar_mock.return_value = {"calendar_sent": 1}
        community_mock.return_value = {"community_sent": 2, "community_failed": 0}

        result = await reminders.run_reminders("Bearer secret-for-tests")

        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["tax_alerts_sent"], 2)
        self.assertEqual(result["calendar_sent"], 1)
        self.assertEqual(result["community_sent"], 2)
        self.assertEqual(result["community_failed"], 0)
        send_mock.assert_awaited_once_with()
        alerts_mock.assert_awaited_once_with()
        calendar_mock.assert_awaited_once_with()
        community_mock.assert_awaited_once_with()

    @patch.object(reminders, "CRON_SECRET", "secret-for-tests")
    async def test_services_continue_when_one_fails(self):
        from contextlib import ExitStack
        services = ("send_due_reminders", "send_tax_alerts", "send_due_calendar_reminders", "send_due_community_notices")
        for failure in services:
            with self.subTest(failure=failure), ExitStack() as stack:
                mocks = [stack.enter_context(patch.object(reminders, name, new=AsyncMock(return_value={}))) for name in services]
                mocks[services.index(failure)].side_effect = RuntimeError("test")
                result = await reminders.run_reminders("Bearer secret-for-tests")
                self.assertEqual(result["status"], "partial_failure")
                for mock in mocks:
                    mock.assert_awaited_once_with()

    @patch.object(reminders, "CRON_SECRET", "secret-for-tests")
    async def test_invalid_bearer_is_rejected(self):
        with self.assertRaises(HTTPException) as context:
            await reminders.run_reminders("Bearer wrong-secret")

        self.assertEqual(context.exception.status_code, 401)

    @patch.object(reminders, "CRON_SECRET", "")
    async def test_missing_configuration_returns_service_unavailable(self):
        with self.assertRaises(HTTPException) as context:
            await reminders.run_reminders(None)

        self.assertEqual(context.exception.status_code, 503)

    def test_route_accepts_get_and_post(self):
        methods = {
            method
            for route in reminders.router.routes
            if route.path == "/internal/reminders/run"
            for method in route.methods
        }

        self.assertEqual(methods, {"GET", "POST"})


if __name__ == "__main__":
    unittest.main()
