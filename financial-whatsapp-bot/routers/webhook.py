import asyncio
import json
import logging
from collections import deque

import dependencies
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response

from config import META_WEBHOOK_VERIFY_TOKEN, DEBUG
from core.ia import process_ai_and_send,process_ai_and_send_Twillio
from services.message_router import route_message, split_message
from services.whatsapp import (
    WhatsAppAPIError,
    normalize_phone,
    send_text,
    verify_webhook_signature,
)

logger = logging.getLogger("financial")

router = APIRouter()

_MAX_RECENT_MESSAGE_IDS = 10_000
_recent_message_ids: deque[str] = deque()
_recent_message_id_set: set[str] = set()


def _remember_message(message_id: str) -> bool:
    """Registra un mensaje y devuelve False cuando Meta ya lo había enviado."""
    if not message_id:
        return True
    if message_id in _recent_message_id_set:
        return False

    if len(_recent_message_ids) >= _MAX_RECENT_MESSAGE_IDS:
        oldest_id = _recent_message_ids.popleft()
        _recent_message_id_set.discard(oldest_id)

    _recent_message_ids.append(message_id)
    _recent_message_id_set.add(message_id)
    return True


def _extract_message_text(incoming: dict) -> str | None:
    """Extrae texto normal o la etiqueta de una respuesta interactiva."""
    message_type = incoming.get("type")
    if message_type == "text":
        return incoming.get("text", {}).get("body", "").strip() or None
    if message_type == "button":
        button = incoming.get("button", {})
        return (button.get("text") or button.get("payload") or "").strip() or None
    if message_type == "interactive":
        interactive = incoming.get("interactive", {})
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        return (reply.get("title") or reply.get("id") or "").strip() or None
    return None





@router.get("/webhook/whatsapp")
async def verify_whatsapp_webhook(
    mode: str | None = Query(default=None, alias="hub.mode"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
):
    """Responde al desafío que Meta utiliza al registrar el webhook."""
    if (
        mode == "subscribe"
        and META_WEBHOOK_VERIFY_TOKEN
        and token == META_WEBHOOK_VERIFY_TOKEN
        and challenge is not None
    ):
        logger.info("Webhook de Meta verificado correctamente")
        return Response(content=challenge, media_type="text/plain")

    raise HTTPException(status_code=403, detail="Token de verificación inválido")


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """Recibe mensajes y estados de WhatsApp Cloud API."""
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not verify_webhook_signature(raw_body, signature):
        logger.warning("Webhook de Meta rechazado por firma inválida")
        raise HTTPException(status_code=401, detail="Firma de webhook inválida")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="JSON inválido") from error

    if payload.get("object") != "whatsapp_business_account":
        return {"status": "ignored"}

    processed_messages = 0

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            for status in value.get("statuses", []):
                logger.info(
                    "Estado WhatsApp %s para %s",
                    status.get("status", "unknown"),
                    status.get("id", "unknown"),
                )

            for incoming in value.get("messages", []):
                message_id = incoming.get("id", "")
                if not _remember_message(message_id):
                    logger.info("Webhook duplicado ignorado: %s", message_id)
                    continue

                phone = normalize_phone(incoming.get("from", ""))
                if not phone:
                    logger.warning("Mensaje de Meta sin teléfono válido")
                    continue

                message = _extract_message_text(incoming)
                if message is None:
                    try:
                        await send_text(
                            phone,
                            "Por ahora solo puedo procesar mensajes de texto. "
                            "Escríbeme tu consulta y te ayudo 😊",
                        )
                    except WhatsAppAPIError as error:
                        logger.error("No se pudo responder al mensaje no textual: %s", error)
                    processed_messages += 1
                    continue

                logger.info("Mensaje de WhatsApp recibido desde %s", phone)
                response_text = await asyncio.to_thread(route_message, phone, message)

                try:
                    if response_text == "__AI_QUERY__":
                        await send_text(phone, "🤔 Déjame pensar tu respuesta...")
                        background_tasks.add_task(
                            process_ai_and_send,
                            phone,
                            message,
                            dependencies.ollama_available,
                        )
                    else:
                        for part in split_message(response_text, 4000):
                            await send_text(phone, part)
                        logger.info("Respuesta enviada a %s", phone)
                except WhatsAppAPIError as error:
                    logger.error("No se pudo enviar la respuesta a %s: %s", phone, error)

                processed_messages += 1

    return {"status": "received", "processed_messages": processed_messages}
