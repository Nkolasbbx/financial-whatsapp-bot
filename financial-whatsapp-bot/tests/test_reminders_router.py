import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from routers import reminders


class ReminderRouterTests(unittest.IsolatedAsyncioTestCase):
    @patch.object(reminders, "CRON_SECRET", "secret-for-tests")
    @patch.object(reminders, "send_due_reminders", new_callable=AsyncMock)
    async def test_valid_bearer_executes_reminders(self, send_mock):
        send_mock.return_value = {"status": "completed", "sent": 1}

        result = await reminders.run_reminders("Bearer secret-for-tests")

        self.assertEqual(result["sent"], 1)
        send_mock.assert_awaited_once_with()

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
