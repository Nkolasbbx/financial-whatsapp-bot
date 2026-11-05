import unittest
from unittest.mock import AsyncMock, patch

from services.whatsapp import WhatsAppAPIError, send_template, send_text


class WhatsAppTests(unittest.IsolatedAsyncioTestCase):
    @patch("services.whatsapp._post_message", new_callable=AsyncMock)
    async def test_send_text_builds_text_payload(self, post_message_mock):
        post_message_mock.return_value = {"messages": [{"id": "wamid.text"}]}

        result = await send_text("+56911111111", "Hola")

        self.assertEqual(result["messages"][0]["id"], "wamid.text")
        post_message_mock.assert_awaited_once_with({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": "56911111111",
            "type": "text",
            "text": {
                "preview_url": False,
                "body": "Hola",
            },
        })

    async def test_send_template_rejects_empty_name(self):
        with self.assertRaises(WhatsAppAPIError):
            await send_template("+56911111111", "", "es_CL", [])

    @patch("services.whatsapp._post_message", new_callable=AsyncMock)
    async def test_approved_community_template_payload(self, post):
        from core.community_events import build_notice_template_parameters
        params = build_notice_template_parameters({
            "title": "Curso gratuito", "category": "taller",
            "event_at": "2030-03-20T13:00:00+00:00", "location": "Centro municipal",
        }, "InnovaRecoleta")
        await send_template("+56911111111", "aviso_convocatoria_comunitaria", "es_CL", params)
        post.assert_awaited_once_with({
            "messaging_product": "whatsapp", "to": "56911111111", "type": "template",
            "template": {
                "name": "aviso_convocatoria_comunitaria", "language": {"code": "es_CL"},
                "components": [{"type": "body", "parameters": [
                    {"type": "text", "text": value} for value in
                    ("InnovaRecoleta", "Curso gratuito", "Taller", "20 de marzo de 2030 a las 10:00", "Centro municipal")
                ]}],
            },
        })


if __name__ == "__main__":
    unittest.main()
