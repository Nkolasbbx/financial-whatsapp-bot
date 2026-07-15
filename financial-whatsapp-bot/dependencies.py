import logging
import httpx
from contextlib import asynccontextmanager
from sentence_transformers import SentenceTransformer
from fastapi import FastAPI
from twilio.rest import Client as TwilioClient
from supabase import create_client, Client as SupabaseClient
import os
from config import (
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    OLLAMA_URL,
    OLLAMA_MODEL,
    SUPABASE_URL,
    SUPABASE_KEY,
    EMBEDDING_MODEL_NAME,
)

logger = logging.getLogger("financial")

twilio_client: TwilioClient | None = None
ollama_available: bool = False
supabase: SupabaseClient | None = None
embedding_model: SentenceTransformer | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize clients on startup."""
    global twilio_client, ollama_available, supabase, embedding_model

    # 1. Inicialización de Twilio
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
        twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        logger.info("✅ Twilio client initialized")
    else:
        logger.warning("⚠️ Twilio credentials not set - running in test mode")

    # 2. Inicialización de IA (Control Inteligente: Ollama vs Groq)
    if OLLAMA_URL and "groq.com" in OLLAMA_URL.lower():
        # ☁️ MODO NUBE: Si la URL apunta a Groq, asumimos conexión exitosa sin buscar /api/tags
        ollama_available = True
        logger.info(f"✅ Groq Cloud conectado exitosamente - model: {OLLAMA_MODEL}")
    else:
        # 🚇 MODO LOCAL/NGROK: Flujo original para chequear el Ollama de tu compañero
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{OLLAMA_URL}/api/tags", timeout=5)
                if r.status_code == 200:
                    models = [m["name"] for m in r.json().get("models", [])]
                    if any(OLLAMA_MODEL in m for m in models):
                        ollama_available = True
                        logger.info(f"✅ Ollama connected - model: {OLLAMA_MODEL}")
                    else:
                        logger.warning(f"⚠️ Ollama running but model {OLLAMA_MODEL} not found. Available: {models}")
                else:
                    logger.warning(f"⚠️ Servidor de IA respondió con código {r.status_code}")
        except Exception:
            logger.warning("⚠️ Ollama not running - AI chat disabled.")

    # 3. 📦 Eager Loading del Modelo de Embeddings (Evita el lag en consultas)
    try:
        logger.info("📦 Precargando Modelo de Embeddings en la RAM global...")
        
        # Oculta advertencias molestas de enlaces en Linux
        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        
        # 💡 NOTA: Cambia local_files_only a True una vez que el modelo se haya bajado la primera vez
        embedding_model = SentenceTransformer(
            EMBEDDING_MODEL_NAME,
            local_files_only=False 
        )
        logger.info("✅ Modelo de Embeddings montado y listo en memoria RAM")
    except Exception as e:
        logger.error(f"❌ Error crítico al precargar SentenceTransformer: {e}")
        embedding_model = None

    # 4. Inicialización de Supabase
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
            logger.info("✅ Supabase client initialized")
        except Exception as e:
            logger.error(f"❌ Supabase error: {e}")
    else:
        logger.warning("⚠️ Supabase credentials not set - using in-memory storage")

    yield