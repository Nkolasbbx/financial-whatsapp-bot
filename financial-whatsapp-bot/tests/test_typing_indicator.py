"""Los tres puntos de "escribiendo…" reemplazan al aviso "Déjame pensar"."""
import unittest
from unittest.mock import AsyncMock, patch

from routers import webhook
from services import whatsapp
from services.whatsapp import WhatsAppAPIError

PHONE = "+56911112222"


class SendTypingIndicatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_payload_follows_the_meta_format(self):
        with patch.object(whatsapp, "_post_message", new=AsyncMock(return_value={"success": True})) as post:
            await whatsapp.send_typing_indicator("wamid.ABC")
        self.assertEqual(post.await_args.args[0], {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": "wamid.ABC",
            "typing_indicator": {"type": "text"},
        })

    async def test_without_message_id_it_raises_instead_of_calling_meta(self):
        with patch.object(whatsapp, "_post_message", new=AsyncMock()) as post:
            with self.assertRaises(WhatsAppAPIError):
                await whatsapp.send_typing_indicator("")
        post.assert_not_awaited()


class ProgressNoticeTests(unittest.IsolatedAsyncioTestCase):
    async def _notice(self, message_id, typing_error=None, text_error=None):
        with patch.object(webhook, "send_typing_indicator", new=AsyncMock(side_effect=typing_error)) as typing, \
             patch.object(webhook, "send_text", new=AsyncMock(side_effect=text_error)) as texto:
            await webhook._send_progress_notice(PHONE, "🤔 Déjame pensar tu respuesta...", message_id)
        return typing, texto

    async def test_typing_indicator_replaces_the_text_message(self):
        typing, texto = await self._notice("wamid.1")
        typing.assert_awaited_once_with("wamid.1")
        texto.assert_not_awaited()

    async def test_if_meta_rejects_the_indicator_the_text_is_sent(self):
        typing, texto = await self._notice("wamid.1", typing_error=WhatsAppAPIError("no soportado"))
        typing.assert_awaited_once()
        texto.assert_awaited_once_with(PHONE, "🤔 Déjame pensar tu respuesta...")

    async def test_without_message_id_the_text_is_sent(self):
        typing, texto = await self._notice(None)
        typing.assert_not_awaited()
        texto.assert_awaited_once()

    async def test_a_failure_of_both_never_raises(self):
        await self._notice("wamid.1", typing_error=WhatsAppAPIError("x"), text_error=WhatsAppAPIError("y"))

    async def test_the_acknowledgement_of_a_complaint_keeps_its_text(self):
        # "Tienes razón, déjame explicarlo de otra forma..." no pasa message_id:
        # reconoce la queja del usuario y debe verse como mensaje.
        typing, texto = await self._notice(None)
        texto.assert_awaited_once()
        typing.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
