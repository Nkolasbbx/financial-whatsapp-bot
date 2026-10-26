import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from services import google_calendar

EVENT = {
    "id": "11111111-2222-3333-4444-555555555555",
    "comuna": "Recoleta",
    "title": "Taller de marketing digital",
    "category": "taller",
    "description": "Aprende a vender por redes sociales.",
    "location": "Centro de Emprendimiento",
    "link": "https://recoleta.cl/taller",
    "event_at": "2030-03-20T13:00:00+00:00",
    "notice_at": "2030-03-19T13:00:00+00:00",
    "status": "scheduled",
}
GOOGLE_ID = "11111111222233334444555555555555"


class BuildEventTests(unittest.TestCase):
    def test_event_body_uses_local_time_and_deterministic_id(self):
        body = google_calendar.build_google_event(EVENT)

        self.assertEqual(body["id"], GOOGLE_ID)
        self.assertEqual(body["summary"], "[Taller] Taller de marketing digital")
        self.assertEqual(body["start"]["dateTime"], "2030-03-20T10:00:00-03:00")
        self.assertEqual(body["end"]["dateTime"], "2030-03-20T11:00:00-03:00")
        self.assertEqual(body["location"], "Centro de Emprendimiento")
        self.assertIn("https://recoleta.cl/taller", body["description"])
        self.assertEqual(body["extendedProperties"]["private"]["financial_id"], EVENT["id"])


@patch.object(google_calendar, "_access_token", new=AsyncMock(return_value="token"))
class GoogleRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_id_is_updated_with_patch(self):
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "POST":
                return httpx.Response(409, json={"error": {"message": "duplicate"}})
            return httpx.Response(200, json={"id": GOOGLE_ID})

        await google_calendar.upsert_google_event(
            EVENT,
            "municipio@group.calendar.google.com",
            transport=httpx.MockTransport(handler),
        )

        self.assertEqual([request.method for request in requests], ["POST", "PATCH"])
        self.assertTrue(str(requests[1].url).endswith(f"/events/{GOOGLE_ID}"))
        self.assertIn("municipio%40group.calendar.google.com", str(requests[0].url))
        self.assertEqual(requests[0].headers["Authorization"], "Bearer token")
        patch_body = json.loads(requests[1].content)
        self.assertNotIn("id", patch_body)
        self.assertEqual(patch_body["status"], "confirmed")

    async def test_deleting_a_missing_event_is_not_an_error(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(410))

        await google_calendar.delete_google_event(EVENT, "cal-id", transport=transport)

    async def test_server_error_raises(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(403, text="forbidden"))

        with self.assertRaises(google_calendar.GoogleCalendarError):
            await google_calendar.upsert_google_event(EVENT, "cal-id", transport=transport)


class SyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_sync_makes_no_calls(self):
        with (
            patch.object(google_calendar, "sync_enabled", return_value=False),
            patch.object(google_calendar, "upsert_google_event", new=AsyncMock()) as upsert_mock,
            patch.object(google_calendar, "set_google_sync_result") as result_mock,
        ):
            status = await google_calendar.sync_community_event(EVENT)

        self.assertEqual(status, "disabled")
        upsert_mock.assert_not_awaited()
        result_mock.assert_not_called()

    async def test_failure_is_saved_as_failed(self):
        with (
            patch.object(google_calendar, "sync_enabled", return_value=True),
            patch.object(google_calendar, "calendar_id_for_comuna", return_value="cal-id"),
            patch.object(
                google_calendar,
                "upsert_google_event",
                new=AsyncMock(side_effect=google_calendar.GoogleCalendarError("403")),
            ),
            patch.object(google_calendar, "set_google_sync_result") as result_mock,
        ):
            status = await google_calendar.sync_community_event(EVENT)

        self.assertEqual(status, "failed")
        result_mock.assert_called_once_with(EVENT["id"], "failed", "403")

    async def test_cancelled_event_is_deleted(self):
        cancelled = dict(EVENT, status="cancelled")
        with (
            patch.object(google_calendar, "sync_enabled", return_value=True),
            patch.object(google_calendar, "calendar_id_for_comuna", return_value="cal-id"),
            patch.object(google_calendar, "delete_google_event", new=AsyncMock()) as delete_mock,
            patch.object(google_calendar, "set_google_sync_result") as result_mock,
        ):
            status = await google_calendar.sync_community_event(cancelled)

        self.assertEqual(status, "synced")
        delete_mock.assert_awaited_once_with(cancelled, "cal-id")
        result_mock.assert_called_once_with(EVENT["id"], "synced", None)

    async def test_comuna_without_calendar_is_marked_disabled(self):
        with (
            patch.object(google_calendar, "sync_enabled", return_value=True),
            patch.object(google_calendar, "calendar_id_for_comuna", return_value=None),
            patch.object(google_calendar, "set_google_sync_result") as result_mock,
        ):
            status = await google_calendar.sync_community_event(EVENT)

        self.assertEqual(status, "disabled")
        result_mock.assert_called_once_with(EVENT["id"], "disabled", None)


if __name__ == "__main__":
    unittest.main()
