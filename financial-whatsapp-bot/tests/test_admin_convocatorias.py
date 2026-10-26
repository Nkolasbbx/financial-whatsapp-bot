import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from routers import admin_convocatorias as panel
from schemas.community_events import CommunityEventRequest

ACCOUNT = {"comuna": "Recoleta", "nombre": "InnovaRecoleta"}
EVENT_ID = "11111111-2222-3333-4444-555555555555"
REQUEST = SimpleNamespace(
    app=SimpleNamespace(state=SimpleNamespace(redis=object())),
    headers={"x-forwarded-proto": "https", "host": "financial.up.railway.app"},
    url=SimpleNamespace(scheme="http", netloc="financial.up.railway.app"),
)


async def _run_immediately(function, *args, **kwargs):
    return function(*args, **kwargs)


def _iso(delta):
    return (datetime.now(timezone.utc) + delta).isoformat()


def _stored(**overrides):
    event = {
        "id": EVENT_ID,
        "comuna": "Recoleta",
        "created_by": "InnovaRecoleta",
        "title": "Taller de marketing digital",
        "category": "taller",
        "description": "Aprende a vender por redes sociales.",
        "location": None,
        "link": None,
        "event_at": _iso(timedelta(days=10)),
        "notice_at": _iso(timedelta(days=9)),
        "status": "scheduled",
        "notice_sent_at": None,
        "google_sync_status": "pending",
    }
    event.update(overrides)
    return event


def _payload(**overrides):
    tomorrow = datetime.now(timezone.utc) + timedelta(days=10)
    data = {
        "title": "Taller de marketing digital",
        "category": "taller",
        "description": "Aprende a vender por redes sociales.",
        "event_at": tomorrow.strftime("%Y-%m-%dT10:00"),
        "notice_at": (tomorrow - timedelta(days=1)).strftime("%Y-%m-%dT10:00"),
    }
    data.update(overrides)
    return CommunityEventRequest(**data)


class AdminApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patches = [
            patch.object(panel, "get_admin_session_account", new=AsyncMock(return_value=ACCOUNT)),
            patch.object(panel, "validate_admin_csrf_token", new=AsyncMock(return_value=True)),
            patch.object(panel, "run_in_threadpool", new=_run_immediately),
            patch.object(panel, "sync_community_event", new=AsyncMock(return_value="disabled")),
        ]
        self.mocks = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)

    async def test_missing_session_is_rejected(self):
        self.mocks[0].return_value = None
        with self.assertRaises(HTTPException) as context:
            await panel.crear(_payload(), REQUEST, "session", "csrf")
        self.assertEqual(context.exception.status_code, 401)

    async def test_request_without_valid_csrf_is_rejected(self):
        self.mocks[1].return_value = False
        with (
            patch.object(panel, "create_community_event") as create_mock,
            self.assertRaises(HTTPException) as context,
        ):
            await panel.crear(_payload(), REQUEST, "session", None)
        self.assertEqual(context.exception.status_code, 403)
        create_mock.assert_not_called()

    async def test_invalid_payload_returns_every_error_and_creates_nothing(self):
        payload = CommunityEventRequest(title="", category="", description="", event_at="", notice_at="")
        with (
            patch.object(panel, "create_community_event") as create_mock,
            self.assertRaises(HTTPException) as context,
        ):
            await panel.crear(payload, REQUEST, "session", "csrf")

        self.assertEqual(context.exception.status_code, 400)
        self.assertGreaterEqual(len(context.exception.detail), 5)
        create_mock.assert_not_called()

    async def test_valid_payload_uses_the_account_comuna(self):
        payload = CommunityEventRequest(**_payload().model_dump(), comuna="El Bosque")
        create_mock = Mock(return_value=_stored())
        with patch.object(panel, "create_community_event", new=create_mock):
            result = await panel.crear(payload, REQUEST, "session", "csrf")

        comuna, created_by, data = create_mock.call_args.args
        self.assertEqual(comuna, "Recoleta")
        self.assertEqual(created_by, "InnovaRecoleta")
        self.assertEqual(data["title"], "Taller de marketing digital")
        self.assertEqual(result.notice_status, "programado")
        self.assertTrue(result.editable)
        self.assertEqual(result.google_sync_status, "disabled")

    async def test_edit_after_notice_was_sent_is_rejected(self):
        sent = _stored(notice_at=_iso(timedelta(hours=-1)), notice_sent_at=_iso(timedelta(minutes=-50)))
        with (
            patch.object(panel, "get_community_event", return_value=sent),
            patch.object(panel, "update_community_event") as update_mock,
            self.assertRaises(HTTPException) as context,
        ):
            await panel.editar(EVENT_ID, _payload(), REQUEST, "session", "csrf")

        self.assertEqual(context.exception.status_code, 409)
        update_mock.assert_not_called()

    async def test_event_from_another_comuna_is_not_found(self):
        with (
            patch.object(panel, "get_community_event", return_value=None) as get_mock,
            self.assertRaises(HTTPException) as context,
        ):
            await panel.editar(EVENT_ID, _payload(), REQUEST, "session", "csrf")

        self.assertEqual(context.exception.status_code, 404)
        get_mock.assert_called_once_with(EVENT_ID, "Recoleta")

    async def test_invalid_uuid_is_not_found_without_querying(self):
        with (
            patch.object(panel, "get_community_event") as get_mock,
            self.assertRaises(HTTPException) as context,
        ):
            await panel.cancelar("no-es-un-uuid", REQUEST, "session", "csrf")

        self.assertEqual(context.exception.status_code, 404)
        get_mock.assert_not_called()

    async def test_past_activity_cannot_be_cancelled(self):
        past = _stored(event_at=_iso(timedelta(hours=-2)), notice_at=_iso(timedelta(days=-1)))
        with (
            patch.object(panel, "get_community_event", return_value=past),
            patch.object(panel, "cancel_community_event") as cancel_mock,
            self.assertRaises(HTTPException) as context,
        ):
            await panel.cancelar(EVENT_ID, REQUEST, "session", "csrf")

        self.assertEqual(context.exception.status_code, 409)
        cancel_mock.assert_not_called()

    async def test_cancel_syncs_google_and_returns_cancelled_event(self):
        cancelled = _stored(status="cancelled")
        with (
            patch.object(panel, "get_community_event", return_value=_stored()),
            patch.object(panel, "cancel_community_event", return_value=cancelled),
            patch.object(panel, "get_delivery_summary", return_value={}),
        ):
            result = await panel.cancelar(EVENT_ID, REQUEST, "session", "csrf")

        self.assertEqual(result.notice_status, "cancelado")
        self.assertFalse(result.cancellable)
        self.mocks[3].assert_awaited_once_with(cancelled)

    async def test_list_queries_only_the_account_comuna_including_cancelled(self):
        list_mock = Mock(return_value=[_stored(), _stored(id="22222222-2222-3333-4444-555555555555", status="cancelled")])
        with (
            patch.object(panel, "list_community_events_between", new=list_mock),
            patch.object(panel, "get_delivery_summary", return_value={}),
        ):
            result = await panel.listar(
                REQUEST,
                start=datetime(2099, 9, 1, tzinfo=timezone.utc),
                end=datetime(2099, 10, 1, tzinfo=timezone.utc),
                financial_admin_session="session",
            )

        self.assertEqual(list_mock.call_args.args[0], "Recoleta")
        self.assertTrue(list_mock.call_args.kwargs["include_cancelled"])
        self.assertEqual([event.notice_status for event in result], ["programado", "cancelado"])


class FeedTests(unittest.IsolatedAsyncioTestCase):
    TOKEN = "x" * 40

    def _accounts(self):
        return {"recoleta": dict(ACCOUNT, ics_token=self.TOKEN), "elbosque": {"comuna": "El Bosque", "ics_token": ""}}

    async def test_invalid_token_returns_404(self):
        with patch.object(panel, "ADMIN_ACCOUNTS", self._accounts()):
            response = await panel.feed_ics("otro-token")
        self.assertEqual(response.status_code, 404)

    async def test_short_configured_token_is_ignored(self):
        with patch.object(panel, "ADMIN_ACCOUNTS", {"recoleta": dict(ACCOUNT, ics_token="corto")}):
            response = await panel.feed_ics("corto")
        self.assertEqual(response.status_code, 404)

    async def test_valid_token_returns_calendar_of_its_comuna(self):
        list_mock = Mock(return_value=[_stored()])
        with (
            patch.object(panel, "ADMIN_ACCOUNTS", self._accounts()),
            patch.object(panel, "list_community_events_since", new=list_mock),
            patch.object(panel, "run_in_threadpool", new=_run_immediately),
        ):
            response = await panel.feed_ics(self.TOKEN)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.media_type.startswith("text/calendar"))
        self.assertEqual(list_mock.call_args.args[0], "Recoleta")
        self.assertIn(b"BEGIN:VEVENT", response.body)


class PageTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_renders_calendar_modal_and_csrf(self):
        account = dict(ACCOUNT, ics_token="t" * 40)
        with (
            patch.object(panel, "get_admin_session_account", new=AsyncMock(return_value=account)),
            patch.object(panel, "get_or_create_admin_csrf_token", new=AsyncMock(return_value="csrf-123")),
            patch.object(panel, "count_notice_recipients", return_value=7),
            patch.object(panel, "run_in_threadpool", new=_run_immediately),
        ):
            response = await panel.pagina(REQUEST, "session")

        body = response.body.decode()
        self.assertIn('id="community-calendar"', body)
        self.assertIn('data-recipients="7"', body)
        self.assertIn('content="csrf-123"', body)
        self.assertIn('id="community-comuna" value="Recoleta" readonly', body)
        self.assertIn("https://financial.up.railway.app/admin/convocatorias/feed/", body)
        self.assertIn("calendar.google.com/calendar/render?cid=webcal", body)
        self.assertIn('href="/admin/convocatorias"', body)

    async def test_page_without_session_redirects_to_login(self):
        with patch.object(panel, "get_admin_session_account", new=AsyncMock(return_value=None)):
            response = await panel.pagina(REQUEST, None)
        self.assertEqual(response.status_code, 303)


if __name__ == "__main__":
    unittest.main()
