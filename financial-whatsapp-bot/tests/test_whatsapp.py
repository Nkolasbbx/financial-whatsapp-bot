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


if __name__ == "__main__":
    unittest.main()
