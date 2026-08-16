import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dependencies import lifespan

import logging
from fastapi import FastAPI
from dependencies import lifespan
from routers import reminders, test, webhook
from db.users import get_messages
from config import DEBUG

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="FinancIAl WhatsApp Bot", lifespan=lifespan)

if DEBUG:
    from routers import webhook_twilio as webhook
    logging.getLogger("financial").info("🐛 Modo DEBUG: usando webhook de Twilio")
else:
    from routers import webhook
    logging.getLogger("financial").info("🚀 Modo producción: usando webhook de Meta")

app.include_router(webhook.router)
app.include_router(test.router)
app.include_router(reminders.router)


@app.get("/")
async def health():
    from dependencies import meta_whatsapp_configured, ollama_available
    from config import OLLAMA_MODEL
    return {
        "status": "running",
        "service": "FinancIAl WhatsApp Bot",
        "mode": "debug (twilio)" if DEBUG else "production (meta)",
        "version": "1.1.0-meta",
        "whatsapp_meta": "configured" if meta_whatsapp_configured() else "not configured",
        "ollama": f"connected ({OLLAMA_MODEL})" if ollama_available else "not running",
    }
