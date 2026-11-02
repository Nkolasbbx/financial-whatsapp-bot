"""Fase 1 de optimización: menos accesos a Supabase sin cambiar respuestas."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from starlette.requests import Request

import dependencies
from db import reminders
from routers import webhook
from services import message_router
from services.message_router import route_message
from services.whatsapp import WhatsAppAPIError
from tests.profile_fixtures import COMPLETE_PROFILE


class RecordingTable:
    def __init__(self, calls, name, data=None):
        self.calls, self.name, self.data = calls, name, data or []
        self.operation = "select"
        self.payload = None

    def select(self, *args, **kwargs):
        self.operation = "select"
        return self

    def update(self, payload):
        self.operation, self.payload = "update", payload
        return self

    def upsert(self, payload, **kwargs):
        self.operation, self.payload = "upsert", payload
        return self

    def __getattr__(self, method):
        return lambda *args, **kwargs: self

    def execute(self):
        self.calls.append((self.name, self.operation, self.payload))
        return SimpleNamespace(data=self.data)


class RecordingClient:
    def __init__(self, rows=None):
        self.calls = []
        self.rows = rows or {}

    def table(self, name):
        return RecordingTable(self.calls, name, self.rows.get(name))


class EnqueueBeforeNoticeTests(unittest.IsolatedAsyncioTestCase):
    async def _webhook(self, route_result, send_side_effect=None):
        payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
            "messages": [{"id": "wamid.1", "from": "56911111111", "type": "text", "text": {"body": "hola"}}],
        }}]}]}
        order = []
        redis = SimpleNamespace(
            set=AsyncMock(return_value=True), eval=AsyncMock(),
            enqueue_job=AsyncMock(side_effect=lambda *a, **k: order.append("enqueue")),
        )
        app = FastAPI()
        app.state.redis = redis

        async def receive():
            return {"type": "http.request", "body": json.dumps(payload).encode()}

        async def send_text(phone, text):
            order.append("notice")
            if send_side_effect:
                raise send_side_effect

        request = Request({"type": "http", "method": "POST", "path": "/webhook/whatsapp", "headers": [], "app": app}, receive)
        with (
            # Independiente del .env local: rate limit por la RPC (mockeada).
            patch.object(webhook, "OPT_REDIS_RATE_LIMIT", False),
            patch.object(webhook, "get_message_context", return_value=None),
            patch.object(webhook, "verify_webhook_signature", return_value=True),
            patch.object(webhook, "is_user_blocked", return_value=False),
            patch.object(webhook, "get_pending_confirmation", new_callable=AsyncMock, return_value=None),
            patch.object(webhook, "check_message_rate_limit", return_value={"allowed": True}),
            patch.object(webhook, "route_message", return_value=route_result),
            patch.object(webhook, "send_text", side_effect=send_text),
            patch.object(webhook, "get_user", return_value={"roadmap": []}),
        ):
            reply = await webhook.whatsapp_webhook(request)
        return reply, order, redis

    async def test_ai_and_financial_jobs_are_enqueued_before_the_notice(self):
        for result in ("__AI_QUERY__", "__FINANCIAL_PARSE__", "__AI_QUERY_WITH_CONTEXT__"):
            _, order, _ = await self._webhook(result)
            self.assertEqual(order, ["enqueue", "notice"], result)

    async def test_failed_notice_does_not_release_the_job_lock(self):
        reply, order, redis = await self._webhook("__AI_QUERY__", WhatsAppAPIError("caído"))
        self.assertEqual(order, ["enqueue", "notice"])
        self.assertEqual(reply["processed_messages"], 1)
        # El worker libera el lock: el webhook no debe hacerlo si ya encoló.
        redis.eval.assert_not_awaited()


class ReminderWritesReuseUserTests(unittest.TestCase):
    def test_roadmap_activity_with_loaded_user_only_updates(self):
        client = RecordingClient()
        user = {"reminders_enabled": True, "roadmap": [{"done": True}, {"done": False}]}
        with patch.object(dependencies, "supabase_admin", client):
            reminders.record_roadmap_activity("+56911111111", user=user)

        self.assertEqual([(name, op) for name, op, _ in client.calls], [("users", "update")])
        self.assertEqual(client.calls[0][2]["reminder_count"], 0)

    def test_roadmap_activity_without_user_keeps_reading(self):
        client = RecordingClient({"users": [{"reminders_enabled": False, "roadmap": []}]})
        with patch.object(dependencies, "supabase_admin", client):
            reminders.record_roadmap_activity("+56911111111")

        self.assertEqual([(name, op) for name, op, _ in client.calls], [("users", "select"), ("users", "update")])

    def test_reminder_reply_with_user_id_skips_user_lookup(self):
        client = RecordingClient({"reminder_deliveries": [{"id": "delivery-1"}]})
        with patch.object(dependencies, "supabase_admin", client):
            replied = reminders.record_incoming_reminder_reply("+56911111111", None, user_id="user-1")

        self.assertTrue(replied)
        self.assertNotIn("users", [name for name, _, _ in client.calls])


class MilestoneSavesOnlyRoadmapTests(unittest.TestCase):
    def test_marking_a_milestone_updates_roadmap_columns_without_upsert(self):
        from core.roadmaps import mark_hito_done

        user = {
            **COMPLETE_PROFILE, "id": "user-1", "phone": "+56911111111", "onboarding_step": "done",
            "roadmap": [{"title": "Primer hito", "desc": "Completar", "done": False},
                        {"title": "Segundo hito", "desc": "Completar", "done": False}],
        }
        client = RecordingClient({"users": [user]})
        with patch.object(dependencies, "supabase", client):
            mark_hito_done(user, message_router._save_roadmap_fields)

        self.assertEqual([(name, op) for name, op, _ in client.calls], [("users", "update")])
        payload = client.calls[0][2]
        self.assertTrue(set(payload) <= set(message_router._ROADMAP_FIELDS) | {"updated_at"})
        self.assertTrue(payload["roadmap"][0]["done"])
        self.assertNotIn("nombre_completo", payload)

    def test_router_uses_field_update_for_milestones(self):
        with (
            patch.object(message_router, "mark_hito_done", return_value="ok") as mark,
            patch.object(message_router, "get_pending_milestone", return_value={"title": "x"}),
            patch.object(message_router, "record_roadmap_activity"),
            patch.object(message_router, "get_user", return_value={
                **COMPLETE_PROFILE, "phone": "+56911111111", "onboarding_step": "done", "roadmap": [],
            }),
        ):
            route_message("+56911111111", "listo")

        self.assertIs(mark.call_args.args[1], message_router._save_roadmap_fields)


if __name__ == "__main__":
    unittest.main()
