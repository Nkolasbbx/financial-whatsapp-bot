import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from db import users as users_db
from routers import webhook
from services import audio_consent as ac

PHONE = "+56911112222"


class FakeRedis:
    def __init__(self):
        self.data = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, key):
        self.data.pop(key, None)

    async def eval(self, script, numkeys, key, token):
        self.data.pop(key, None)
        return 1


class ModuleTests(unittest.IsolatedAsyncioTestCase):
    def test_consent_state(self):
        self.assertIsNone(ac.consent_state(None))
        self.assertIsNone(ac.consent_state({}))
        self.assertIsNone(ac.consent_state({"audio_consent": None}))
        self.assertTrue(ac.consent_state({"audio_consent": True}))
        self.assertFalse(ac.consent_state({"audio_consent": False}))

    def test_prompt_explains_processing_and_asks_for_acceptance(self):
        prompt = ac.consent_prompt()
        self.assertEqual(prompt["type"], "buttons")
        self.assertIn("convertir tu audio en texto", prompt["body"])
        self.assertIn("no se utilizará para clonar ni imitar tu voz", prompt["body"])
        self.assertIn("texto", prompt["body"].split("Si no aceptas")[1])
        self.assertEqual([i for i, _ in prompt["options"]], [ac.ACCEPT_ID, ac.REJECT_ID])

    def test_echo_shows_the_transcript_with_a_way_out(self):
        echo = ac.echo_widget("quiero saber de patentes")
        self.assertIn("Esto entendí de tu audio", echo["body"])
        self.assertIn("quiero saber de patentes", echo["body"])
        self.assertEqual(echo["options"][0][0], ac.WRONG_ID)
        self.assertLessEqual(len(echo["options"][0][1]), 20)  # límite de WhatsApp

    def test_choice_detection(self):
        self.assertEqual(ac.audio_choice("audio_acepto"), "accept")
        self.assertEqual(ac.audio_choice("Aceptar audios."), "accept")
        self.assertEqual(ac.audio_choice("audio_rechazo"), "reject")
        self.assertEqual(ac.audio_choice("audio_incorrecto"), "wrong")
        self.assertIsNone(ac.audio_choice("hola"))
        self.assertIsNone(ac.audio_choice(None))

    def test_saying_it_was_not_what_i_said_counts_as_the_button(self):
        for frase in ("No es lo que dije.", "no, no es lo que dije", "Eso no es lo que dije", "no dije eso"):
            self.assertEqual(ac.audio_choice(frase), "wrong", frase)
        self.assertIsNone(ac.audio_choice("no es lo que necesito"))

    async def test_pending_audio_is_kept_once(self):
        redis = FakeRedis()
        await ac.stash_pending_audio(redis, PHONE, "media-1")
        self.assertEqual(await ac.pop_pending_audio(redis, PHONE), "media-1")
        self.assertIsNone(await ac.pop_pending_audio(redis, PHONE))


class SetConsentTests(unittest.TestCase):
    def _client(self, update_data, upsert_data=None):
        client = MagicMock()
        table = client.table.return_value
        table.update.return_value.eq.return_value.execute.return_value = SimpleNamespace(data=update_data)
        table.upsert.return_value.execute.return_value = SimpleNamespace(data=upsert_data or [])
        return client, table

    def test_updates_an_existing_user(self):
        client, table = self._client([{"phone": PHONE}])
        with patch("dependencies.supabase_admin", client):
            self.assertTrue(users_db.set_audio_consent(PHONE, True))
        fields = table.update.call_args.args[0]
        self.assertIs(fields["audio_consent"], True)
        self.assertIn("audio_consent_at", fields)
        table.upsert.assert_not_called()

    def test_creates_the_user_when_the_first_message_was_an_audio(self):
        client, table = self._client([], [{"phone": PHONE}])
        with patch("dependencies.supabase_admin", client):
            self.assertTrue(users_db.set_audio_consent(PHONE, False))
        row = table.upsert.call_args.args[0]
        self.assertEqual(row["onboarding_step"], 0)
        self.assertIs(row["audio_consent"], False)

    def test_database_error_returns_false(self):
        client = MagicMock()
        client.table.side_effect = RuntimeError("db")
        with patch("dependencies.supabase_admin", client):
            self.assertFalse(users_db.set_audio_consent(PHONE, True))


class WebhookAudioFlowTests(unittest.IsolatedAsyncioTestCase):
    """Recorrido completo por el webhook con los bordes (Meta, base) simulados."""

    def setUp(self):
        self.redis = FakeRedis()
        self.counter = 0

    def _request(self, message):
        self.counter += 1
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {"messages": [
                {"from": "56911112222", "id": f"wamid.T{self.counter}", **message}
            ]}}]}],
        }
        body = json.dumps(payload).encode()
        request = MagicMock()
        request.body = AsyncMock(return_value=body)
        request.headers = {"X-Hub-Signature-256": "x"}
        request.app.state.redis = self.redis
        request.base_url = "http://test/"
        return request

    async def _run(self, message, consent, transcript="quiero saber de patentes"):
        user = {"id": "u1", "onboarding_step": "done", "audio_consent": consent}
        mocks = SimpleNamespace(
            send_text=AsyncMock(),
            buttons=AsyncMock(),
            lista=AsyncMock(),
            transcribe=AsyncMock(return_value=transcript),
            route=MagicMock(return_value="respuesta del bot"),
            set_consent=MagicMock(return_value=True),
        )
        patches = [
            patch.object(webhook, "verify_webhook_signature", return_value=True),
            patch.object(webhook, "is_user_blocked", return_value=False),
            patch.object(webhook, "get_user", return_value=user),
            patch.object(webhook, "check_message_rate_limit", return_value={"allowed": True}),
            patch.object(webhook, "_has_open_expert_request", return_value=False),
            patch.object(webhook, "send_text", mocks.send_text),
            patch.object(webhook, "send_interactive_buttons", mocks.buttons),
            patch.object(webhook, "send_interactive_list", mocks.lista),
            patch.object(webhook, "_transcribe_incoming_audio", mocks.transcribe),
            patch.object(webhook, "route_message", mocks.route),
            patch.object(webhook, "set_audio_consent", mocks.set_consent),
        ]
        for p in patches:
            p.start()
        try:
            await webhook.whatsapp_webhook(self._request(message))
        finally:
            for p in patches:
                p.stop()
        return mocks

    @staticmethod
    def _audio(media_id="media-1"):
        return {"type": "audio", "audio": {"id": media_id}}

    @staticmethod
    def _button(button_id):
        return {"type": "interactive", "interactive": {"button_reply": {"id": button_id, "title": button_id}}}

    async def test_first_audio_asks_for_consent_and_is_not_transcribed(self):
        m = await self._run(self._audio("media-1"), consent=None)
        m.transcribe.assert_not_awaited()
        m.route.assert_not_called()
        self.assertIn("clonar", m.buttons.await_args.args[1])
        self.assertEqual(await ac.pop_pending_audio(self.redis, PHONE), "media-1")

    async def test_accepted_user_sees_the_transcript_before_the_answer(self):
        m = await self._run(self._audio(), consent=True)
        echo_body = m.buttons.await_args_list[0].args[1]
        self.assertIn("Esto entendí de tu audio", echo_body)
        self.assertIn("quiero saber de patentes", echo_body)
        m.route.assert_called_once()
        self.assertEqual(m.route.call_args.args[1], "quiero saber de patentes")
        m.send_text.assert_awaited()  # la respuesta, después del eco

    async def test_rejected_user_keeps_using_text_and_audio_is_not_transcribed(self):
        m = await self._run(self._audio(), consent=False)
        m.transcribe.assert_not_awaited()
        m.route.assert_not_called()
        self.assertIn("aceptar audios", m.send_text.await_args.args[1])

    async def test_accepting_processes_the_audio_that_was_waiting(self):
        await ac.stash_pending_audio(self.redis, PHONE, "media-9")
        m = await self._run(self._button(ac.ACCEPT_ID), consent=True)
        m.set_consent.assert_called_once_with(PHONE, True)
        self.assertEqual(m.transcribe.await_args.args[0]["audio"]["id"], "media-9")
        self.assertIn("Esto entendí de tu audio", m.buttons.await_args_list[0].args[1])
        m.route.assert_called_once()

    async def test_accepting_without_a_waiting_audio_only_confirms(self):
        m = await self._run(self._button(ac.ACCEPT_ID), consent=True)
        m.transcribe.assert_not_awaited()
        m.route.assert_not_called()
        self.assertEqual(m.send_text.await_args.args[1], ac.ACCEPTED_TEXT)

    async def test_rejecting_saves_the_decision_and_drops_the_waiting_audio(self):
        await ac.stash_pending_audio(self.redis, PHONE, "media-9")
        m = await self._run(self._button(ac.REJECT_ID), consent=False)
        m.set_consent.assert_called_once_with(PHONE, False)
        m.transcribe.assert_not_awaited()
        self.assertIsNone(await ac.pop_pending_audio(self.redis, PHONE))

    async def test_saying_it_was_not_what_i_said_discards_any_pending_confirmation(self):
        await self.redis.set(f"pending_confirm:{PHONE}", "reiniciar")
        m = await self._run(self._button(ac.WRONG_ID), consent=True)
        self.assertIsNone(await self.redis.get(f"pending_confirm:{PHONE}"))
        m.route.assert_not_called()
        self.assertIn("descarté ese audio", m.send_text.await_args.args[1])

    async def test_saying_it_by_voice_discards_the_audio_without_echo_or_routing(self):
        await self.redis.set(f"pending_confirm:{PHONE}", "reiniciar")
        m = await self._run(self._audio(), consent=True, transcript="No es lo que dije.")
        self.assertIsNone(await self.redis.get(f"pending_confirm:{PHONE}"))
        m.buttons.assert_not_awaited()  # no se repite el eco de la frase de corrección
        m.route.assert_not_called()
        self.assertIn("descarté ese audio", m.send_text.await_args.args[1])

    async def test_failed_transcription_offers_to_resend_or_write(self):
        m = await self._run(self._audio(), consent=True, transcript=None)
        m.route.assert_not_called()
        texto = m.send_text.await_args_list[0].args[1]
        self.assertIn("grabarla de nuevo", texto)
        self.assertIn("por texto", texto)


if __name__ == "__main__":
    unittest.main()
