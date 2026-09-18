import unittest
from unittest.mock import AsyncMock, patch

import worker


class ReminderCronJobTests(unittest.IsolatedAsyncioTestCase):
    @patch.object(worker, "release_phone_lock", new_callable=AsyncMock)
    @patch.object(
        worker,
        "process_financial_movement_and_send",
        new_callable=AsyncMock,
    )
    async def test_financial_job_processes_and_releases_phone_lock(
        self,
        process_mock,
        release_mock,
    ):
        redis = object()

        await worker.process_financial_movement_task(
            {"redis": redis},
            "+56911111111",
            "vendí 40.000 en empanadas",
            lock_token="lock-1",
        )

        process_mock.assert_awaited_once_with(
            "+56911111111",
            "vendí 40.000 en empanadas",
        )
        release_mock.assert_awaited_once_with(
            redis,
            "+56911111111",
            "lock-1",
        )

    def test_financial_job_is_registered(self):
        self.assertIn(
            worker.process_financial_movement_task,
            worker.WorkerSettings.functions,
        )

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
