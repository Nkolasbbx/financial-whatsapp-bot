# routers/webhook_twilio.py
import asyncio
import logging
import dependencies
from fastapi import APIRouter, Request, Response, BackgroundTasks
from twilio.twiml.messaging_response import MessagingResponse
from services.message_router import route_message, split_message
from core.ia import process_ai_and_send_Twillio
from db.rate_limits import (
    RATE_LIMIT_WARNING,
    check_message_rate_limit,
    is_rate_limit_exempt,
)
from db.users import get_last_user_message, get_user, save_user

logger = logging.getLogger("financial")
router = APIRouter()

@router.post("/webhook/whatsapp")
async def whatsapp_webhook_twilio(request: Request, background_tasks: BackgroundTasks):
    """Handle incoming WhatsApp messages from Twilio."""
    form = await request.form()
    phone = form.get("From", "")
    message = form.get("Body", "").strip()
    
    phone_clean = phone.replace("whatsapp:", "").strip()
    twiml = MessagingResponse()

    if not is_rate_limit_exempt(message):
        rate_limit = await asyncio.to_thread(
            check_message_rate_limit,
            phone_clean,
        )
        if not rate_limit["allowed"]:
            logger.warning(
                "Mensaje Twilio bloqueado por rate limit: phone=%s "
                "retry_after=%s",
                phone_clean,
                rate_limit["retry_after_seconds"],
            )
            if rate_limit["notify_user"]:
                twiml.message(RATE_LIMIT_WARNING)
            return Response(content=str(twiml), media_type="application/xml")

    response_text = await asyncio.to_thread(
        route_message,
        phone_clean,
        message,
    )
    
    try:
        # ── CASO 1: Consulta normal ──
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
        
        # ── CASO 2: NUEVO - Con contexto del hito ──
        elif response_text == "__AI_QUERY_WITH_CONTEXT__":
            twiml.message("🤔 Te ayudo con este hito...")
            
            user = get_user(phone_clean)
            hito_context = None
            if user:
                from core.roadmaps import extract_hito_context
                hito_context = extract_hito_context(user)
            
            background_tasks.add_task(
                process_ai_and_send_Twillio,
                phone,
                phone_clean,
                message,
                lambda p: get_user(p),
                lambda p, d: save_user(p, d),
                dependencies.twilio_client,
                dependencies.ollama_available,
                hito_context=hito_context,  # ← NUEVO
                reformulate_mode=False,
            )
        
        # ── CASO 3: NUEVO - Con reformulación ──
        elif response_text == "__AI_QUERY_WITH_REFORMULATE__":
            last_message = await asyncio.to_thread(
                get_last_user_message,
                phone_clean,
            )

            if not last_message:
                twiml.message(
                    "No pude encontrar tu pregunta anterior. "
                    "¿Puedes escribirla nuevamente?"
                )
            else:
                twiml.message("Tienes razón, déjame explicarlo de otra forma...")
                background_tasks.add_task(
                    process_ai_and_send_Twillio,
                    phone,
                    phone_clean,
                    last_message,
                    lambda p: get_user(p),
                    lambda p, d: save_user(p, d),
                    dependencies.twilio_client,
                    dependencies.ollama_available,
                    hito_context=None,
                    reformulate_mode=True,  # ← NUEVO
                )
        
        # ── CASO 4: Respuesta interactiva ──
        else:
            if isinstance(response_text, dict):
                # Manejo de botones/listas en Twilio (simplificado)
                body = response_text.get("body", "")
                twiml.message(body)
            else:
                for part in split_message(response_text, 4000):
                    twiml.message(part)

    except Exception as e:
        logger.error(f"Error en webhook_twilio: {e}")
        twiml.message("Tuve un problema. ¿Puedes intentar de nuevo?")

    return Response(content=str(twiml), media_type="application/xml")
