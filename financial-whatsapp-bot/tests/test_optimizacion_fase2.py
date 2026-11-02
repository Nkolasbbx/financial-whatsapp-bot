"""Fase 2 de optimización: contexto en una lectura, rate limit en Redis,
estados de entrega en una llamada y caché de catálogos."""
import json
import os
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from starlette.requests import Request

import dependencies
from db import catalog_cache, delivery_status, message_context, rate_limits
from db.message_context import NOT_LOADED, MessageContext, parse_message_context
from routers import webhook
from services import message_router
from services.message_router import route_message
from tests.profile_fixtures import COMPLETE_PROFILE

USER_ID = "11111111-2222-3333-4444-555555555555"
PHONE = "56911111111"


def make_user(**changes):
    return {**COMPLETE_PROFILE, "id": USER_ID, "phone": PHONE, "onboarding_step": "done",
            "roadmap": [{"title": "Primer hito", "desc": "Completar", "done": False}],
            "reminder_count": 0, "bloqueado": False, **changes}


EMPTY_FORMALIZATION = {"diagnostic": None, "items": []}


def make_context(**changes):
    return MessageContext(user=make_user(), formalization=EMPTY_FORMALIZATION, **changes)


def fail(name):
    def raiser(*args, **kwargs):
        raise AssertionError(f"{name} no debía consultarse: el contexto ya lo trae")
    return raiser


class ParseMessageContextTests(unittest.TestCase):
    def payload(self, **changes):
        return {"version": 1, "user": make_user(roadmap='[{"title": "A", "done": false}]'),
                "expert_request": None, "formalization": EMPTY_FORMALIZATION,
                "fund_session": None, "fund_definition": None,
                "financial_session": None, "calendar_session": None, **changes}

    def test_normalizes_roadmap_text_and_keeps_sessions(self):
        context = parse_message_context(self.payload(
            financial_session={"user_id": USER_ID, "state": "confirming_creation"},
            calendar_session={"user_id": USER_ID, "state": "waiting_date"},
        ))
        self.assertEqual(context.user["roadmap"], [{"title": "A", "done": False}])
        self.assertEqual(context.financial_session["state"], "confirming_creation")
        self.assertEqual(context.calendar_session["state"], "waiting_date")
        self.assertEqual(context.formalization, EMPTY_FORMALIZATION)

    def test_inactive_fund_session_is_treated_as_absent(self):
        context = parse_message_context(self.payload(
            fund_session={"id": "s", "status": "cancelled", "pending_field_key": "rut"},
            fund_definition={"field_key": "rut"},
        ))
        self.assertIsNone(context.fund_session)
        self.assertIsNone(context.fund_definition)

    def test_active_fund_session_keeps_its_pending_definition(self):
        context = parse_message_context(self.payload(
            fund_session={"id": "s", "status": "collecting_data", "pending_field_key": "rut"},
            fund_definition={"field_key": "rut", "answer_type": "boolean"},
        ))
        self.assertEqual(context.fund_definition["field_key"], "rut")

    def test_formalization_error_falls_back_to_the_previous_read(self):
        context = parse_message_context(self.payload(formalization={"error": "P0001"}))
        self.assertIs(context.formalization, NOT_LOADED)
        context = parse_message_context(self.payload(formalization=None))
        self.assertIs(context.formalization, NOT_LOADED)

    def test_unknown_phone_and_invalid_payloads(self):
        self.assertIsNone(parse_message_context({"version": 1, "user": None}).user)
        self.assertIsNone(parse_message_context({"version": 2, "user": {}}))
        self.assertIsNone(parse_message_context("texto"))
        self.assertIsNotNone(parse_message_context([self.payload()]))


class GetMessageContextTests(unittest.TestCase):
    def test_disabled_flag_does_not_call_supabase(self):
        client = MagicMock()
        with patch.object(dependencies, "supabase_admin", client), \
                patch("config.OPT_MESSAGE_CONTEXT_RPC", False):
            self.assertIsNone(message_context.get_message_context(PHONE))
        client.rpc.assert_not_called()

    def test_rpc_failure_returns_none_to_use_previous_path(self):
        client = MagicMock()
        client.rpc.return_value.execute.side_effect = RuntimeError("function does not exist")
        with patch.object(dependencies, "supabase_admin", client), \
                patch("config.OPT_MESSAGE_CONTEXT_RPC", True):
            self.assertIsNone(message_context.get_message_context(PHONE))

    def test_success_sends_phone_and_the_same_as_of_date(self):
        client = MagicMock()
        client.rpc.return_value.execute.return_value = SimpleNamespace(
            data={"version": 1, "user": make_user(), "formalization": EMPTY_FORMALIZATION})
        with patch.object(dependencies, "supabase_admin", client), \
                patch("config.OPT_MESSAGE_CONTEXT_RPC", True), \
                patch("config.FORMALIZATION_DIAGNOSTICS_ENABLED", True):
            context = message_context.get_message_context(PHONE)
        name, params = client.rpc.call_args.args
        self.assertEqual(name, "get_message_context_v1")
        self.assertEqual(params["p_phone"], PHONE)
        self.assertTrue(params["p_include_formalization"])
        self.assertIn("p_as_of", params)
        self.assertEqual(context.user["id"], USER_ID)


class RouteWithContextTests(unittest.TestCase):
    """La decisión es la misma con y sin contexto; con contexto no hay lecturas."""

    def legacy_patches(self, context):
        return {
            "get_user": MagicMock(return_value=context.user),
            "get_active_fund_session": MagicMock(return_value=context.fund_session),
            "get_requirement_definitions": MagicMock(
                return_value={context.fund_definition["field_key"]: context.fund_definition}
                if context.fund_definition else {}),
            "get_financial_session": MagicMock(return_value=context.financial_session),
            "get_calendar_session": MagicMock(return_value=context.calendar_session),
        }

    def run_route(self, message, context, use_context, extra=None):
        with ExitStack() as stack:
            stack.enter_context(patch.object(message_router, "FINANCIAL_MOVEMENTS_ENABLED", True))
            stack.enter_context(patch.object(message_router, "FORMALIZATION_DIAGNOSTICS_ENABLED", True))
            stack.enter_context(patch.object(message_router, "record_incoming_reminder_reply", return_value=False))
            stack.enter_context(patch.object(message_router, "record_roadmap_activity"))
            stack.enter_context(patch.object(message_router, "cancel_fund_session"))
            stack.enter_context(patch.object(message_router, "clear_calendar_session"))
            stack.enter_context(patch.object(message_router, "clear_financial_session"))
            if use_context:
                for name in ("get_user", "get_active_fund_session", "get_requirement_definitions",
                             "get_financial_session", "get_calendar_session"):
                    stack.enter_context(patch.object(message_router, name, side_effect=fail(name)))
                stack.enter_context(patch("core.expert_flow.get_open_expert_request",
                                          side_effect=fail("get_open_expert_request")))
                stack.enter_context(patch("core.formalization_flow.get_formalization_context",
                                          side_effect=fail("get_formalization_context")))
            else:
                for name, mock in self.legacy_patches(context).items():
                    stack.enter_context(patch.object(message_router, name, mock))
                stack.enter_context(patch("core.expert_flow.get_open_expert_request",
                                          return_value=context.expert_request))
                stack.enter_context(patch("core.formalization_flow.get_formalization_context",
                                          return_value=context.formalization))
            mocks = {name: stack.enter_context(patch.object(message_router, name, **kwargs))
                     for name, kwargs in (extra or {}).items()}
            result = route_message(PHONE, message, context=context if use_context else None)
        return result, mocks

    def assert_same_in_both_modes(self, message, context, extra=None, check=None):
        outcomes = []
        for use_context in (False, True):
            result, mocks = self.run_route(message, context, use_context, extra)
            outcomes.append(result)
            if check:
                check(mocks)
        self.assertEqual(outcomes[0], outcomes[1])
        return outcomes[1]

    def test_menu_without_any_extra_read(self):
        result = self.assert_same_in_both_modes("menu", make_context())
        self.assertEqual(result["type"], "list")

    def test_free_question_goes_to_ai_in_both_modes(self):
        self.assertEqual(self.assert_same_in_both_modes("¿cómo saco patente?", make_context()), "__AI_QUERY__")

    def test_short_answer_with_financial_draft_uses_its_session(self):
        session = {"user_id": USER_ID, "state": "confirming_creation", "draft": {}}

        def check(mocks):
            self.assertEqual(mocks["handle_financial_message"].call_args.args[2], session)

        self.assert_same_in_both_modes(
            "sí", make_context(financial_session=session),
            extra={"handle_financial_message": {"return_value": "fin"}}, check=check)

    def test_number_with_calendar_draft_uses_its_session(self):
        session = {"user_id": USER_ID, "state": "waiting_time"}

        def check(mocks):
            self.assertEqual(mocks["handle_calendar_message"].call_args.args[2], session)

        self.assert_same_in_both_modes(
            "3", make_context(calendar_session=session),
            extra={"handle_calendar_message": {"return_value": "cal"}}, check=check)

    def test_yes_with_pending_fund_question_uses_session_and_definition(self):
        session = {"id": "s", "user_id": USER_ID, "status": "collecting_data", "pending_field_key": "tiene_rut"}
        definition = {"field_key": "tiene_rut", "answer_type": "boolean", "options": []}

        def check(mocks):
            args = mocks["handle_fund_message"].call_args.args
            self.assertEqual((args[2], args[3]), (session, definition))

        self.assert_same_in_both_modes(
            "sí", make_context(fund_session=session, fund_definition=definition),
            extra={"handle_fund_message": {"return_value": "fondo"}}, check=check)

    def test_fund_entry_discards_prefetched_sessions_it_clears(self):
        session = {"user_id": USER_ID, "state": "confirming_creation", "draft": {}}
        seen = []

        def spy(message, financial_session=None):
            seen.append(financial_session)
            return False

        self.run_route("postular a fondos", make_context(financial_session=session), True, extra={
            "should_handle_financial_message": {"side_effect": spy},
            "handle_fund_message": {"return_value": "fondos"},
        })
        # Finanzas se evalúa después de que fondos borró las sesiones: debe ver
        # None, igual que al releer de la base, y no el borrador ya eliminado.
        self.assertEqual(seen, [None])

    def test_stale_context_reads_again(self):
        context = make_context()
        context.stale = True
        result, _ = self.run_route("menu", context, False)
        self.assertEqual(result["type"], "list")


class WebhookContextTests(unittest.IsolatedAsyncioTestCase):
    async def run_webhook(self, context, route_result="ok"):
        payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
            "messages": [{"id": "wamid.1", "from": PHONE, "type": "text", "text": {"body": "menu"}}],
        }}]}]}
        redis = SimpleNamespace(set=AsyncMock(return_value=True), eval=AsyncMock(return_value=[1, 0, 0]),
                                enqueue_job=AsyncMock())
        app = FastAPI()
        app.state.redis = redis

        async def receive():
            return {"type": "http.request", "body": json.dumps(payload).encode()}

        request = Request({"type": "http", "method": "POST", "path": "/webhook/whatsapp", "headers": [], "app": app}, receive)
        route = MagicMock(return_value=route_result)
        sent = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(webhook, "verify_webhook_signature", return_value=True))
            stack.enter_context(patch.object(webhook, "get_message_context", return_value=context))
            blocked = stack.enter_context(patch.object(webhook, "is_user_blocked", return_value=False))
            stack.enter_context(patch.object(webhook, "get_pending_confirmation", new_callable=AsyncMock, return_value=None))
            rpc_limit = stack.enter_context(patch.object(webhook, "check_message_rate_limit", return_value={"allowed": True}))
            stack.enter_context(patch.object(webhook, "OPT_REDIS_RATE_LIMIT", True))
            stack.enter_context(patch.object(webhook, "route_message", route))
            stack.enter_context(patch.object(webhook, "send_text", AsyncMock(side_effect=lambda p, t: sent.append(t))))
            stack.enter_context(patch.object(webhook, "_send_response", AsyncMock()))
            await webhook.whatsapp_webhook(request)
        return route, blocked, rpc_limit, redis, sent

    async def test_context_replaces_block_check_and_reaches_the_router(self):
        context = make_context()
        route, blocked, rpc_limit, redis, _ = await self.run_webhook(context)
        blocked.assert_not_called()
        rpc_limit.assert_not_called()  # el límite va por Redis
        self.assertIs(route.call_args.kwargs["context"], context)
        self.assertEqual(redis.eval.await_count, 2)  # rate limit + liberar lock

    async def test_blocked_user_from_context_gets_notice_and_no_routing(self):
        context = MessageContext(user=make_user(bloqueado=True))
        route, _, _, redis, sent = await self.run_webhook(context)
        route.assert_not_called()
        self.assertEqual(sent, [webhook.BLOCKED_USER_MESSAGE])
        redis.eval.assert_awaited()  # el lock se libera

    async def test_without_context_uses_previous_block_check(self):
        _, blocked, _, _, _ = await self.run_webhook(None)
        blocked.assert_called_once()

    def test_audio_helpers_reuse_context_until_it_is_stale(self):
        context = make_context(expert_request={"id": "r", "status": "in_progress"})
        context.user["audio_consent"] = True
        with patch.object(webhook, "get_user", side_effect=fail("get_user")):
            self.assertTrue(webhook._has_open_expert_request(PHONE, context))
            self.assertTrue(webhook._audio_consent(PHONE, context))
        context.stale = True
        with patch.object(webhook, "get_user", return_value={"audio_consent": False}) as get_user:
            self.assertFalse(webhook._audio_consent(PHONE, context))
        get_user.assert_called_once()


class RedisRateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_maps_script_result_and_sends_window_parameters(self):
        redis = SimpleNamespace(eval=AsyncMock(return_value=[0, 1, 60]))
        with patch.object(rate_limits, "RATE_LIMIT_ENABLED", True), \
                patch.object(rate_limits, "RATE_LIMIT_MAX_MESSAGES", 2), \
                patch.object(rate_limits, "RATE_LIMIT_WINDOW_SECONDS", 60), \
                patch.object(rate_limits, "RATE_LIMIT_BLOCK_SECONDS", 60):
            decision = await rate_limits.check_message_rate_limit_redis(redis, PHONE)
        self.assertEqual(decision, {"allowed": False, "notify_user": True, "retry_after_seconds": 60})
        args = redis.eval.await_args.args
        self.assertEqual(args[1], 2)
        self.assertEqual(args[2:4], (f"rate_limit:{PHONE}:ts", f"rate_limit:{PHONE}:blocked"))
        self.assertEqual(args[5:8], (2, 60000, 60000))

    async def test_redis_failure_and_disabled_limit_allow_the_message(self):
        redis = SimpleNamespace(eval=AsyncMock(side_effect=ConnectionError("caído")))
        with patch.object(rate_limits, "RATE_LIMIT_ENABLED", True):
            self.assertTrue((await rate_limits.check_message_rate_limit_redis(redis, PHONE))["allowed"])
        with patch.object(rate_limits, "RATE_LIMIT_ENABLED", False):
            self.assertTrue((await rate_limits.check_message_rate_limit_redis(redis, PHONE))["allowed"])


@unittest.skipUnless(os.getenv("REDIS_TEST_URL"), "Define REDIS_TEST_URL para probar el script contra Redis")
class RedisRateLimitScriptTests(unittest.IsolatedAsyncioTestCase):
    """Semántica del script Lua contra un Redis real (no producción)."""

    async def asyncSetUp(self):
        from redis.asyncio import Redis

        self.redis = Redis.from_url(os.environ["REDIS_TEST_URL"])
        self.phone = "test-rate-limit-phone"
        await self.redis.delete(f"rate_limit:{self.phone}:ts", f"rate_limit:{self.phone}:blocked")

    async def asyncTearDown(self):
        await self.redis.delete(f"rate_limit:{self.phone}:ts", f"rate_limit:{self.phone}:blocked")
        await self.redis.aclose()

    async def test_allows_max_then_blocks_once_and_stays_blocked_silently(self):
        with patch.object(rate_limits, "RATE_LIMIT_ENABLED", True), \
                patch.object(rate_limits, "RATE_LIMIT_MAX_MESSAGES", 2):
            decisions = [await rate_limits.check_message_rate_limit_redis(self.redis, self.phone) for _ in range(4)]
        self.assertEqual([d["allowed"] for d in decisions], [True, True, False, False])
        self.assertEqual([d["notify_user"] for d in decisions], [False, False, True, False])
        self.assertGreaterEqual(decisions[3]["retry_after_seconds"], 1)


class DeliveryStatusTests(unittest.IsolatedAsyncioTestCase):
    def test_rpc_receives_event_time_and_returns_owner_table(self):
        client = MagicMock()
        client.rpc.return_value.execute.return_value = SimpleNamespace(data="calendar")
        with patch.object(dependencies, "supabase_admin", client):
            owner = delivery_status.apply_delivery_status("wamid.x", "read", "1767225600", None)
        self.assertEqual(owner, "calendar")
        name, params = client.rpc.call_args.args
        self.assertEqual(name, "apply_delivery_status_v1")
        self.assertEqual(params["p_event_at"], "2026-01-01T00:00:00+00:00")

    def test_missing_rpc_raises_unavailable(self):
        client = MagicMock()
        client.rpc.return_value.execute.side_effect = RuntimeError("PGRST202")
        with patch.object(dependencies, "supabase_admin", client):
            with self.assertRaises(delivery_status.DeliveryStatusUnavailable):
                delivery_status.apply_delivery_status("wamid.x", "read")

    async def test_webhook_uses_one_call_and_falls_back_on_failure(self):
        status = {"id": "wamid.x", "status": "delivered", "timestamp": "1767225600"}
        legacy = {name: MagicMock() for name in (
            "update_reminder_delivery_status", "update_calendar_delivery_status", "update_community_delivery_status")}
        with ExitStack() as stack:
            stack.enter_context(patch.object(webhook, "OPT_DELIVERY_STATUS_RPC", True))
            for name, mock in legacy.items():
                stack.enter_context(patch.object(webhook, name, mock))
            rpc = stack.enter_context(patch.object(webhook, "apply_delivery_status", return_value="reminder"))
            await webhook._apply_status(status, None)
            self.assertEqual(rpc.call_count, 1)
            for mock in legacy.values():
                mock.assert_not_called()

            rpc.side_effect = delivery_status.DeliveryStatusUnavailable("PGRST202")
            await webhook._apply_status(status, None)
            for mock in legacy.values():
                mock.assert_called_once()


class CatalogCacheTests(unittest.TestCase):
    def setUp(self):
        catalog_cache.clear()
        self.addCleanup(catalog_cache.clear)

    def test_second_read_within_ttl_does_not_query_and_returns_copies(self):
        loader = MagicMock(return_value={"rut": {"field_key": "rut"}})
        client = object()
        with patch("config.CATALOG_CACHE_TTL_SECONDS", 600):
            first = catalog_cache.cached("k", client, loader)
            first["rut"]["field_key"] = "modificado"
            second = catalog_cache.cached("k", client, loader)
        loader.assert_called_once()
        self.assertEqual(second["rut"]["field_key"], "rut")

    def test_other_client_none_values_and_zero_ttl_reload(self):
        loader = MagicMock(return_value={"a": 1})
        with patch("config.CATALOG_CACHE_TTL_SECONDS", 600):
            catalog_cache.cached("k", object(), loader)
            catalog_cache.cached("k", object(), loader)
            empty = MagicMock(return_value=None)
            catalog_cache.cached("n", "c", empty)
            catalog_cache.cached("n", "c", empty)
        with patch("config.CATALOG_CACHE_TTL_SECONDS", 0):
            catalog_cache.cached("z", "c", loader)
            catalog_cache.cached("z", "c", loader)
        self.assertEqual(loader.call_count, 4)
        self.assertEqual(empty.call_count, 2)

    def test_fund_definitions_query_once_and_still_filter(self):
        from db import fondos

        executes = []

        class Query:
            def __getattr__(self, name):
                return lambda *a, **k: self

            def execute(self):
                executes.append(1)
                return SimpleNamespace(data=[{"field_key": "rut"}, {"field_key": "edad"}])

        client = SimpleNamespace(table=lambda name: Query())
        with patch.object(fondos, "_admin_client", return_value=client), \
                patch("config.CATALOG_CACHE_TTL_SECONDS", 600):
            self.assertEqual(set(fondos.get_requirement_definitions()), {"rut", "edad"})
            self.assertEqual(set(fondos.get_requirement_definitions(["rut"])), {"rut"})
        self.assertEqual(len(executes), 1)


if __name__ == "__main__":
    unittest.main()
