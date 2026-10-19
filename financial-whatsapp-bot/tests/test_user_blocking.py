import asyncio
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from db import users
from db.users import BLOCKED_USER_MESSAGE, is_user_blocked, reset_user_profile
from routers import webhook


def _client_returning(data):
    client = MagicMock()
    query = client.table.return_value.select.return_value.eq.return_value.limit.return_value
    query.execute.return_value = SimpleNamespace(data=data)
    return client


class IsUserBlockedTests(unittest.TestCase):
    def _check(self, fake_dependencies, phone="+56911111111"):
        with patch.dict(sys.modules, {"dependencies": fake_dependencies}):
            return is_user_blocked(phone)

    def test_blocked_user_returns_true(self):
        client = _client_returning([{"bloqueado": True}])
        fake = SimpleNamespace(supabase=client, supabase_admin=client)

        self.assertTrue(self._check(fake))
        client.table.assert_called_once_with("users")
        client.table.return_value.select.assert_called_once_with("bloqueado")

    def test_enabled_user_returns_false(self):
        client = _client_returning([{"bloqueado": False}])
        fake = SimpleNamespace(supabase=client, supabase_admin=client)

        self.assertFalse(self._check(fake))

    def test_unknown_phone_is_not_blocked(self):
        client = _client_returning([])
        fake = SimpleNamespace(supabase=client, supabase_admin=client)

        self.assertFalse(self._check(fake))

    def test_supabase_error_fails_open(self):
        client = MagicMock()
        client.table.side_effect = RuntimeError("caída")
        fake = SimpleNamespace(supabase=client, supabase_admin=client)

        self.assertFalse(self._check(fake))

    def test_in_memory_mode_reads_users_db(self):
        fake = SimpleNamespace(supabase=None, supabase_admin=None)
        with patch.dict(users.users_db, {"+56922222222": {"bloqueado": True}}, clear=True):
            self.assertTrue(self._check(fake, "+56922222222"))
            self.assertFalse(self._check(fake, "+56933333333"))


class ResetPreservesBlockTests(unittest.TestCase):
    def test_reset_does_not_touch_bloqueado(self):
        client = MagicMock()
        query = client.table.return_value
        query.update.return_value.eq.return_value.execute.return_value = SimpleNamespace(
            data=[{"phone": "+56911111111", "bloqueado": True}]
        )
        fake = SimpleNamespace(supabase=client, supabase_admin=client)
        current_user = {
            "id": "user-1",
            "phone": "+56911111111",
            "bloqueado": True,
            "rubro": "alimentos",
            "onboarding_step": "done",
        }

        with patch.dict(sys.modules, {"dependencies": fake}):
            reset_user_profile("+56911111111", current_user)

        payload = query.update.call_args.args[0]
        self.assertNotIn("bloqueado", payload)
        self.assertIsNone(payload["rubro"])


class BlockedUserWebhookTests(unittest.TestCase):
    def _request(self, payload):
        redis = MagicMock()
        redis.set = AsyncMock(return_value=True)
        redis.enqueue_job = AsyncMock()
        return SimpleNamespace(
            body=AsyncMock(return_value=json.dumps(payload).encode()),
            headers={"X-Hub-Signature-256": "sha256=ok"},
            app=SimpleNamespace(state=SimpleNamespace(redis=redis)),
            base_url="http://testserver/",
        )

    def _payload(self, message):
        return {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {"messages": [message]}}]}],
        }

    def _run(self, payload, blocked):
        with (
            patch.object(webhook, "verify_webhook_signature", return_value=True),
            patch.object(webhook, "is_user_blocked", return_value=blocked),
            patch.object(webhook, "acquire_phone_lock", AsyncMock(return_value="tok")),
            patch.object(webhook, "release_phone_lock", AsyncMock()),
            patch.object(webhook, "get_pending_confirmation", AsyncMock(return_value=None)),
            patch.object(webhook, "check_message_rate_limit", return_value={"allowed": True}),
            patch.object(webhook, "route_message", return_value="Hola") as route_mock,
            patch.object(webhook, "send_text", AsyncMock()) as send_mock,
            patch.object(webhook, "transcribe_audio", AsyncMock()) as transcribe_mock,
        ):
            result = asyncio.run(webhook.whatsapp_webhook(self._request(payload)))
        return result, route_mock, send_mock, transcribe_mock

    def test_blocked_user_only_gets_access_notice(self):
        payload = self._payload({
            "id": "wamid.1", "from": "56911111111", "type": "text",
            "text": {"body": "hola"},
        })

        result, route_mock, send_mock, _ = self._run(payload, blocked=True)

        self.assertEqual(result["processed_messages"], 1)
        route_mock.assert_not_called()
        send_mock.assert_awaited_once()
        self.assertEqual(send_mock.await_args.args[1], BLOCKED_USER_MESSAGE)

    def test_blocked_user_audio_is_not_transcribed(self):
        payload = self._payload({
            "id": "wamid.2", "from": "56911111111", "type": "audio",
            "audio": {"id": "media-1"},
        })

        _, route_mock, send_mock, transcribe_mock = self._run(payload, blocked=True)

        transcribe_mock.assert_not_called()
        route_mock.assert_not_called()
        send_mock.assert_awaited_once()
        self.assertEqual(send_mock.await_args.args[1], BLOCKED_USER_MESSAGE)

    def test_enabled_user_is_routed_normally(self):
        payload = self._payload({
            "id": "wamid.3", "from": "56911111111", "type": "text",
            "text": {"body": "hola"},
        })

        result, route_mock, send_mock, _ = self._run(payload, blocked=False)

        self.assertEqual(result["processed_messages"], 1)
        route_mock.assert_called_once()
        send_mock.assert_awaited()


if __name__ == "__main__":
    unittest.main()
