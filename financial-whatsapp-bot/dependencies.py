import logging
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI
from twilio.rest import Client as TwilioClient
from supabase import create_client, Client as SupabaseClient
import psycopg2
from psycopg2 import pool
import os


from config import (
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    OLLAMA_URL,
    OLLAMA_MODEL,     
    SUPABASE_URL,     
    SUPABASE_KEY,     
    SUPABASE_DB_DSN,  # NUEVO: connection string directo a Postgres (para psycopg2 + pgvector)
    EMBEDDING_MODEL_NAME,
)
 
logger = logging.getLogger("financial")
 
twilio_client: TwilioClient | None = None
ollama_available: bool = False
supabase: SupabaseClient | None = None
embedding_model = None
db_pool: psycopg2.pool.SimpleConnectionPool | None = None  # NUEVO
 
 
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize clients on startup."""
    global twilio_client, ollama_available, supabase, embedding_model, db_pool
 
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
 
 
    # 4. Inicialización de Supabase (cliente REST, para todo lo que no sea RAG)
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
            logger.info("✅ Supabase client initialized")
        except Exception as e:
            logger.error(f"❌ Supabase error: {e}")
    else:
        logger.warning("⚠️ Supabase credentials not set - using in-memory storage")
 
    # 5. 🔌 Pool de conexiones directas a Postgres (para RAG con pgvector + full-text)
    #    El cliente supabase-py (REST) no permite ejecutar SQL crudo con
    #    operadores como <=> o ts_rank, por eso se necesita esta conexión aparte.
    if SUPABASE_DB_DSN:
        try:
            logger.info("🔌 Creando pool de conexiones Postgres para RAG...")
            db_pool = psycopg2.pool.SimpleConnectionPool(
                minconn=1,
                maxconn=5,
                dsn=SUPABASE_DB_DSN,
            )
            logger.info("✅ Pool de conexiones Postgres listo.")
        except Exception as e:
            logger.error(f"❌ Error crítico al crear el pool de Postgres: {e}")
            db_pool = None
    else:
        logger.warning("⚠️ SUPABASE_DB_DSN no configurado - búsqueda RAG (pgvector) deshabilitada")
 
    yield
 
    # ── SHUTDOWN: se ejecuta al apagar la app ──
    if db_pool is not None:
        db_pool.closeall()
        logger.info("🔒 Pool de conexiones Postgres cerrado.")