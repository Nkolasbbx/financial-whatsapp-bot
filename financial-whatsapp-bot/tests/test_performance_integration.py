"""Exercise the real webhook/send boundary with simulated external services."""
import json
import os
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from postgrest import SyncPostgrestClient
from starlette.requests import Request

import dependencies
from routers import webhook
from services import performance as perf
from tests.test_performance import FakeQuery


class PerformanceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def execute_message(self, result, enabled=True):
        payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
            "messages": [{"id": "wamid.private", "from": "56911111111", "type": "text", "text": {"body": "private content"}}],
        }}]}]}
        redis = SimpleNamespace(set=AsyncMock(return_value=True), eval=AsyncMock(), enqueue_job=AsyncMock())
        app = FastAPI()
        app.state.redis = redis
        async def receive():
            return {"type": "http.request", "body": json.dumps(payload).encode()}
        request = Request({"type": "http", "method": "POST", "path": "/webhook/whatsapp", "headers": [], "app": app}, receive)
        query = FakeQuery()
        @perf.observe_route
        def route(*args, **kwargs):
            perf.set_case("menu")
            dependencies.supabase.table("users").select("*").eq("phone", "private").execute()
            return result
        sent_payloads = []
        def transport(request):
            sent_payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"messages": [{"id": "wamid.outgoing.private"}]})
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"PERFORMANCE_METRICS_ENABLED": str(enabled).lower(), "PERFORMANCE_OUTPUT_DIR": ""}))
            stack.enter_context(patch.object(webhook, "verify_webhook_signature", return_value=True))
            stack.enter_context(patch.object(webhook, "is_user_blocked", return_value=False))
            stack.enter_context(patch.object(webhook, "get_pending_confirmation", new_callable=AsyncMock, return_value=None))
            stack.enter_context(patch.object(webhook, "check_message_rate_limit", return_value={"allowed": True}))
            stack.enter_context(patch.object(webhook, "route_message", side_effect=route))
            stack.enter_context(patch("services.whatsapp._messages_url", return_value="https://meta.invalid/messages"))
            stack.enter_context(patch("services.whatsapp.META_WHATSAPP_TOKEN", "test-token"))
            stack.enter_context(patch.object(dependencies, "supabase", perf.instrument_supabase(SimpleNamespace(table=lambda name: query))))
            emit = stack.enter_context(patch.object(perf, "_emit"))
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                stack.enter_context(patch.object(dependencies, "whatsapp_http_client", client))
                reply = await webhook.whatsapp_webhook(request)
            return reply, redis, emit.call_args.args[0] if emit.called else None, sent_payloads

    async def test_real_webhook_counts_db_and_meta_without_changing_response(self):
        widget = {"type": "buttons", "body": "Example menu", "options": [("menu_financial", "Menu")]}
        reply, _, trace, sent = await self.execute_message(widget)
        self.assertEqual(reply, {"status": "received", "processed_messages": 1})
        self.assertEqual(sent[0]["interactive"]["body"]["text"], widget["body"])
        self.assertEqual(trace.case, "menu")
        self.assertEqual(trace.outcome, "completed")
        self.assertEqual(trace.db_operations, {"table:users": 1})
        self.assertEqual(trace.accepted_sends, 1)
        self.assertIn("phone_lock_wait", trace.stages)
        serialized = json.dumps(trace.record())
        for private in ("56911111111", "wamid.private", "private content", "test-token"):
            self.assertNotIn(private, serialized)

    async def test_real_webhook_for_finances_exports_only_safe_trace_metadata(self):
        _, redis, trace, _ = await self.execute_message("__FINANCIAL_PARSE__")
        args = redis.enqueue_job.await_args
        self.assertEqual(args.args[0], "process_financial_movement_task")
        self.assertEqual(args.kwargs["performance_context"]["trace_id"], trace.trace_id)
        self.assertEqual(trace.outcome, "queued")
        self.assertEqual(trace.case, "financial_parse")

    async def test_disabled_measurement_preserves_existing_job_contract(self):
        _, redis, trace, _ = await self.execute_message("__FINANCIAL_PARSE__", enabled=False)
        self.assertIsNone(trace)
        self.assertNotIn("performance_context", redis.enqueue_job.await_args.kwargs)

    async def test_advisor_jobs_are_not_given_measurement_metadata(self):
        _, redis, trace, _ = await self.execute_message("__AI_QUERY__")
        self.assertTrue(trace.excluded)
        self.assertNotIn("performance_context", redis.enqueue_job.await_args.kwargs)
        self.assertEqual(redis.enqueue_job.await_args.args[0], "process_ai_task")

    def test_fastapi_route_signature_is_preserved(self):
        app = FastAPI()
        app.include_router(webhook.router)
        self.assertIn("/webhook/whatsapp", app.openapi()["paths"])

    def test_real_postgrest_builders_keep_identical_queries_and_results(self):
        requests = []
        def transport(request):
            requests.append((request.method, str(request.url), request.content))
            return httpx.Response(200, json=[{"id": "test-id"}])
        with httpx.Client(base_url="https://supabase.invalid/rest/v1", transport=httpx.MockTransport(transport)) as session:
            with patch("postgrest._sync.client.SyncClient", return_value=session):
                raw = SyncPostgrestClient("https://supabase.invalid/rest/v1")
            with patch.dict(os.environ, {"PERFORMANCE_METRICS_ENABLED": "true"}):
                observed = perf.instrument_supabase(raw)
            def run(client):
                return [
                    client.table("users").select("id").eq("phone", "private").limit(1).execute().data,
                    client.table("users").update({"onboarding_step": "done"}).eq("id", "test-id").execute().data,
                    client.rpc("example", {"p_user_id": "test-id"}).execute().data,
                ]
            original = run(raw)
            original_requests = list(requests)
            requests.clear()
            with patch.object(perf, "_emit"):
                with perf.trace_scope(perf.Trace()) as trace:
                    measured = run(observed)
            self.assertEqual(original, measured)
            self.assertEqual(original_requests, requests)
            self.assertEqual(trace.db_operations, {"table:users": 2, "rpc:example": 1})


if __name__ == "__main__":
    unittest.main()
