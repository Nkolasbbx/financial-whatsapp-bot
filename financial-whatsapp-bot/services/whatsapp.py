import hashlib
import hmac
import logging

import httpx

from config import (
    META_APP_SECRET,
    META_GRAPH_API_VERSION,
    META_PHONE_NUMBER_ID,
    META_WHATSAPP_TOKEN,
)

logger = logging.getLogger("financial")


class WhatsAppAPIError(RuntimeError):
    """Error controlado al comunicarse con WhatsApp Cloud API."""


def normalize_phone(phone: str) -> str:
    """Normaliza un teléfono al formato E.164 usado internamente."""
    digits = "".join(character for character in (phone or "") if character.isdigit())
    return f"+{digits}" if digits else ""


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """Valida la firma X-Hub-Signature-256 enviada por Meta."""
    if not META_APP_SECRET or not signature.startswith("sha256="):
        return False

    expected_digest = hmac.new(
        META_APP_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    received_digest = signature.removeprefix("sha256=")
    return hmac.compare_digest(expected_digest, received_digest)


def _messages_url() -> str:
    import dependencies

    if not dependencies.meta_whatsapp_configured():
        raise WhatsAppAPIError("Meta WhatsApp Cloud API no está configurada")

    return (
        f"https://graph.facebook.com/{META_GRAPH_API_VERSION}/"
        f"{META_PHONE_NUMBER_ID}/messages"
    )


async def _post_message(payload: dict) -> dict:
    import dependencies

    headers = {
        "Authorization": f"Bearer {META_WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    client = dependencies.whatsapp_http_client
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30)

    try:
        response = await client.post(_messages_url(), headers=headers, json=payload)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as error:
        logger.error(
            "Meta WhatsApp respondió con %s: %s",
            error.response.status_code,
            error.response.text,
        )
        try:
            meta_error = error.response.json().get("error", {})
            detail = meta_error.get("message", "Error sin detalle")
            code = meta_error.get("code", error.response.status_code)
        except (ValueError, AttributeError):
            detail = "Error sin detalle"
            code = error.response.status_code
        raise WhatsAppAPIError(
            f"Meta rechazó el envío ({code}): {detail}"
        ) from error
    except httpx.HTTPError as error:
        logger.error("Error de conexión con Meta WhatsApp: %s", error)
        raise WhatsAppAPIError("No fue posible conectar con Meta WhatsApp") from error
    finally:
        if owns_client:
            await client.aclose()


async def send_text(phone: str, content: str) -> dict:
    """Envía un mensaje de texto libre mediante WhatsApp Cloud API."""
    recipient = normalize_phone(phone).removeprefix("+")
    if not recipient:
        raise WhatsAppAPIError("El teléfono destinatario no es válido")

    return await _post_message(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": content,
            },
        }
    )


async def send_template(
    phone: str,
    template_name: str,
    language_code: str,
    parameters: list[str] | None = None,
) -> dict:
    """Envía una plantilla previamente aprobada por Meta."""
    recipient = normalize_phone(phone).removeprefix("+")
    if not recipient:
        raise WhatsAppAPIError("El teléfono destinatario no es válido")
    if not template_name.strip() or not language_code.strip():
        raise WhatsAppAPIError("La plantilla o su idioma no son válidos")

    template: dict = {
        "name": template_name,
        "language": {"code": language_code},
    }
    if parameters:
        template["components"] = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": str(value)}
                    for value in parameters
                ],
            }
        ]

    return await _post_message(
        {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "template",
            "template": template,
        }
    )


def extract_provider_message_id(response: dict) -> str | None:
    """Extrae el wamid que Meta devuelve cuando acepta el envío."""
    messages = response.get("messages") or []
    if not messages:
        return None
    return messages[0].get("id")
