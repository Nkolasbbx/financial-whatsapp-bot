import unittest
from unittest.mock import AsyncMock, patch

import worker


class ReminderCronJobTests(unittest.IsolatedAsyncioTestCase):
    @patch.object(worker, "send_due_calendar_reminders", new_callable=AsyncMock)
    @patch.object(worker, "send_due_reminders", new_callable=AsyncMock)
    @patch.object(worker, "send_tax_alerts", new_callable=AsyncMock)
    async def test_runs_all_reminder_services(
        self, alerts_mock, reminders_mock, calendar_mock
    ):
        reminders_mock.return_value = {"status": "completed", "sent": 1}
        alerts_mock.return_value = {"status": "completed", "tax_alerts_sent": 2}
        calendar_mock.return_value = {"calendar_status": "completed", "calendar_sent": 1}

        await worker.run_reminders_job({})

        reminders_mock.assert_awaited_once_with()
        alerts_mock.assert_awaited_once_with()
        calendar_mock.assert_awaited_once_with()

    @patch.object(worker, "send_due_calendar_reminders", new_callable=AsyncMock)
    @patch.object(worker, "send_due_reminders", new_callable=AsyncMock)
    @patch.object(worker, "send_tax_alerts", new_callable=AsyncMock)
    async def test_swallows_exceptions_without_crashing_worker(
        self, alerts_mock, reminders_mock, calendar_mock
    ):
        reminders_mock.side_effect = RuntimeError("boom")

        await worker.run_reminders_job({})  # no debe relanzar

        reminders_mock.assert_awaited_once_with()
        alerts_mock.assert_awaited_once_with()
        calendar_mock.assert_awaited_once_with()

    def test_cron_job_is_registered_hourly(self):
        jobs_by_coroutine = {job.coroutine: job for job in worker.WorkerSettings.cron_jobs}
        job = jobs_by_coroutine[worker.run_reminders_job]

        self.assertEqual(job.minute, 0)
        self.assertEqual(job.hour, set(range(24)))
        self.assertFalse(job.run_at_startup)


if __name__ == "__main__":
    unittest.main()
