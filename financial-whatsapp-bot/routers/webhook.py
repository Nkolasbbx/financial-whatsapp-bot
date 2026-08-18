import asyncio
import json
import logging
from collections import deque

import dependencies
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response

from config import META_WEBHOOK_VERIFY_TOKEN, DEBUG
from core.ia import process_ai_and_send, process_ai_and_send_Twillio
from core.roadmaps import extract_hito_context
from db.reminders import update_reminder_delivery_status
from db.users import get_user
from services.message_router import route_message, split_message
from services.whatsapp import (
    WhatsAppAPIError,
    normalize_phone,
    send_interactive_buttons,
    send_interactive_list,
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
    """Extrae texto normal o el id/etiqueta de una respuesta interactiva.

    Para botones y listas se prioriza el `id` (p. ej. "rubro_textil",
    "sii_si") sobre el título visible, porque la lógica de onboarding
    matchea por id cuando la respuesta vino de un botón/lista.
    """
    message_type = incoming.get("type")
    if message_type == "text":
        return incoming.get("text", {}).get("body", "").strip() or None
    if message_type == "button":
        button = incoming.get("button", {})
        return (button.get("text") or button.get("payload") or "").strip() or None
    if message_type == "interactive":
        interactive = incoming.get("interactive", {})
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        return (reply.get("id") or reply.get("title") or "").strip() or None
    return None


async def _send_response(phone: str, result) -> None:
    """Despacha la respuesta de route_message según su tipo.

    `result` puede ser:
      - str: texto plano (compatibilidad con flujos existentes)
      - dict {"type": "text", "body": ...}
      - dict {"type": "buttons", "body": ..., "options": [(id, titulo), ...]}
      - dict {"type": "list", "body": ..., "button_text": ..., "options": [(id, titulo), ...]}
    """
    if isinstance(result, dict):
        result_type = result.get("type", "text")
        body = result.get("body", "")

        if result_type == "buttons":
            await send_interactive_buttons(phone, body, result["options"])
            return
        if result_type == "list":
            await send_interactive_list(
                phone,
                body,
                result.get("button_text", "Elegir"),
                result["options"],
            )
            return

        # type == "text" u otro no reconocido: cae a texto plano
        for part in split_message(body, 4000):
            await send_text(phone, part)
        return

    # compatibilidad: result sigue siendo un string plano
    for part in split_message(result, 4000):
        await send_text(phone, part)


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
                errors = status.get("errors") or []
                failure_reason = json.dumps(errors, ensure_ascii=False) if errors else None
                try:
                    await asyncio.to_thread(
                        update_reminder_delivery_status,
                        status.get("id", ""),
                        status.get("status", ""),
                        status.get("timestamp"),
                        failure_reason,
                    )
                except Exception as error:
                    logger.error(
                        "No se pudo actualizar el estado del recordatorio %s: %s",
                        status.get("id", "unknown"),
                        error,
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
                reply_to_message_id = incoming.get("context", {}).get("id")
                result = await asyncio.to_thread(
                    route_message,
                    phone,
                    message,
                    reply_to_message_id,
                )

                try:
                    if result == "__AI_QUERY__":
                        await send_text(phone, "🤔 Déjame pensar tu respuesta...")
                        background_tasks.add_task(
                            process_ai_and_send,
                            phone,
                            message,
                            dependencies.ollama_available,
                        )
                    
                    elif result == "__AI_QUERY_WITH_CONTEXT__":
                        await send_text(phone, "🤔 Te ayudo con este hito...")
                        
                        user = await asyncio.to_thread(get_user, phone)
                        hito_context = None
                        if user:
                            from core.roadmaps import extract_hito_context
                            hito_context = extract_hito_context(user)
                        
                        background_tasks.add_task(
                            process_ai_and_send,
                            phone,
                            message,
                            dependencies.ollama_available,
                            hito_context=hito_context,
                            reformulate_mode=False,
                        )
                    
                    elif result == "__AI_QUERY_WITH_REFORMULATE__":
                        await send_text(phone, "Tienes razón, déjame explicarlo de otra forma...")
                        
                        user = await asyncio.to_thread(get_user, phone)
                        last_message = user.get("last_unsatisfied_message", message) if user else message
                        
                        background_tasks.add_task(
                            process_ai_and_send,
                            phone,
                            last_message,
                            dependencies.ollama_available,
                            hito_context=None,
                            reformulate_mode=True,
                        )
                    
                    else:
                        await _send_response(phone, result)
                        logger.info("Respuesta enviada a %s", phone)
                except WhatsAppAPIError as error:
                    logger.error("No se pudo enviar la respuesta a %s: %s", phone, error)

                processed_messages += 1

    return {"status": "received", "processed_messages": processed_messages}