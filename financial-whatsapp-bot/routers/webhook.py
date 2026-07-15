import logging

import dependencies
from fastapi import APIRouter, Request, Response, BackgroundTasks
from twilio.twiml.messaging_response import MessagingResponse

from services.message_router import route_message, split_message
from core.ia import process_ai_and_send
from db.users import get_user, save_user

logger = logging.getLogger("financial")

router = APIRouter()


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle incoming WhatsApp messages from Twilio."""
    form = await request.form()

    phone = form.get("From", "")  # e.g., "whatsapp:+56912345678"
    message = form.get("Body", "").strip()

    logger.info(f"📩 Message from {phone}: {message}")

    # Clean phone number
    phone_clean = phone.replace("whatsapp:", "").strip()

    # Route and get response
    response_text = route_message(phone_clean, message)

    # Build TwiML response
    twiml = MessagingResponse()

    if response_text == "__AI_QUERY__":
        # AI queries: respond immediately with "thinking" message, process in background
        twiml.message("🤔 Déjame pensar tu respuesta...")
        background_tasks.add_task(
            process_ai_and_send,
            phone,
            phone_clean,
            message,
            lambda p: get_user(p),
            lambda p, d: save_user(p, d),
            dependencies.twilio_client,
            dependencies.ollama_available,
        )
    else:
        logger.info(f"📤 Response to {phone}: {response_text[:100]}...")
        if len(response_text) > 4000:
            parts = split_message(response_text, 4000)
            for part in parts:
                twiml.message(part)
        else:
            twiml.message(response_text)

    return Response(content=str(twiml), media_type="application/xml")