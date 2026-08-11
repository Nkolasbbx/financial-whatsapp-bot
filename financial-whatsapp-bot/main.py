import logging
from fastapi import FastAPI
from dependencies import lifespan
from routers import webhook, test
from db.users import get_messages

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="FinancIAl WhatsApp Bot", lifespan=lifespan)

app.include_router(webhook.router)
app.include_router(test.router)


@app.get("/")
async def health():
    from dependencies import meta_whatsapp_configured, ollama_available
    from config import OLLAMA_MODEL
    return {
        "status": "running",
        "service": "FinancIAl WhatsApp Bot",
        "version": "1.1.0-meta",
        "whatsapp_meta": "configured" if meta_whatsapp_configured() else "not configured",
        "ollama": f"connected ({OLLAMA_MODEL})" if ollama_available else "not running",
    }


@app.get('/test/resumen/{phone}')
def test_rag(phone: str):
    historial = get_messages(phone,limit=10)
    if not historial:
        return "No hay mensajes para este numero."
    
    return{
        "phone": phone,
        "mensajes usados": len(historial),
        "mensajes": historial,
    }
