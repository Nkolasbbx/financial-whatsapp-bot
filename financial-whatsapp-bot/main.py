import logging
from fastapi import FastAPI
from dependencies import lifespan
from routers import webhook, test

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="FinancIAl WhatsApp Bot", lifespan=lifespan)

app.include_router(webhook.router)
app.include_router(test.router)


@app.get("/")
async def health():
    from dependencies import twilio_client, ollama_available
    from config import OLLAMA_MODEL
    return {
        "status": "running",
        "service": "FinancIAl WhatsApp Bot",
        "version": "1.0.0-mvp",
        "twilio": "connected" if twilio_client else "not configured",
        "ollama": f"connected ({OLLAMA_MODEL})" if ollama_available else "not running",
    }