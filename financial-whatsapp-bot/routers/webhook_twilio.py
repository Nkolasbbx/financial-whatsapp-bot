# routers/webhook_twilio.py
import logging
import dependencies
from fastapi import APIRouter, Request, Response, BackgroundTasks
from twilio.twiml.messaging_response import MessagingResponse
from services.message_router import route_message, split_message
from core.ia import process_ai_and_send_Twillio
from db.users import get_user, save_user

logger = logging.getLogger("financial")
router = APIRouter()

@router.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle incoming WhatsApp messages from Twilio."""
    form = await request.form()
    phone = form.get("From", "")
    message = form.get("Body", "").strip()
    logger.info(f"📩 Message from {phone}: {message}")

    phone_clean = phone.replace("whatsapp:", "").strip()
    response_text = route_message(phone_clean, message)

    twiml = MessagingResponse()
    if response_text == "__AI_QUERY__":
        twiml.message("🤔 Déjame pensar tu respuesta...")
        background_tasks.add_task(
            process_ai_and_send_Twillio,
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
            for part in split_message(response_text, 4000):
                twiml.message(part)
        else:
            twiml.message(response_text)

    return Response(content=str(twiml), media_type="application/xml")