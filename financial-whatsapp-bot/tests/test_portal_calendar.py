import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from routers import portal_calendar
from schemas.calendar import CalendarEventCreateRequest


async def _run_immediately(function, *args, **kwargs):
    return function(*args, **kwargs)


class PortalCalendarTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_session_is_rejected(self):
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(redis=object()),
            )
        )

        with patch.object(
            portal_calendar,
            "get_session_phone",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as context:
                await portal_calendar._authenticated_user(request, None)

        self.assertEqual(context.exception.status_code, 401)

    async def test_create_uses_user_from_portal_session(self):
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(redis=object()),
            )
        )
        payload = CalendarEventCreateRequest(
            description="Renovar patente municipal",
            event_at=datetime(2099, 9, 20, 15, 30),
            reminder_days_before=3,
        )
        stored_event = {
            "id": "event-1",
            "description": payload.description,
            "event_at": "2099-09-20T18:30:00+00:00",
            "reminder_at": "2099-09-17T18:30:00+00:00",
            "status": "active",
        }

        create_mock = unittest.mock.Mock(return_value=stored_event)
        with (
            patch.object(
                portal_calendar,
                "_authenticated_user",
                new=AsyncMock(return_value={"id": "session-user"}),
            ),
            patch.object(
                portal_calendar,
                "_require_csrf",
                new=AsyncMock(),
            ),
            patch.object(
                portal_calendar,
                "_clear_conversation_draft",
                new=AsyncMock(),
            ),
            patch.object(
                portal_calendar,
                "create_calendar_event",
                new=create_mock,
            ),
            patch.object(
                portal_calendar,
                "run_in_threadpool",
                new=_run_immediately,
            ),
        ):
            result = await portal_calendar.create_event(
                payload,
                request,
                financial_session="session-id",
                csrf_token="csrf-token",
            )

        self.assertEqual(result.id, "event-1")
        self.assertEqual(create_mock.call_args.args[0], "session-user")

    async def test_list_queries_only_authenticated_user(self):
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(redis=object()),
            )
        )
        query_mock = unittest.mock.Mock(return_value=[])

        with (
            patch.object(
                portal_calendar,
                "_authenticated_user",
                new=AsyncMock(return_value={"id": "session-user"}),
            ),
            patch.object(
                portal_calendar,
                "get_calendar_events_between",
                new=query_mock,
            ),
            patch.object(
                portal_calendar,
                "run_in_threadpool",
                new=_run_immediately,
            ),
        ):
            result = await portal_calendar.list_events(
                request,
                start=datetime(2099, 9, 1, tzinfo=timezone.utc),
                end=datetime(2099, 10, 1, tzinfo=timezone.utc),
                financial_session="session-id",
            )

        self.assertEqual(result, [])
        self.assertEqual(query_mock.call_args.args[0], "session-user")


if __name__ == "__main__":
    unittest.main()
