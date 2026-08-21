import logging
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI
from twilio.rest import Client as TwilioClient
from supabase import create_client, Client as SupabaseClient
import psycopg2
from psycopg2 import pool
import os
from contextlib import asynccontextmanager

import httpx
import psycopg2
from fastapi import FastAPI
from psycopg2 import pool
from supabase import Client as SupabaseClient
from supabase import create_client

from config import (
    EMBEDDING_MODEL_NAME,
    META_GRAPH_API_VERSION,
    META_PHONE_NUMBER_ID,
    META_WHATSAPP_TOKEN,
    OLLAMA_MODEL,
    OLLAMA_URL,
    RES_KEY,
    RES_MODEL,
    RES_URL,
    SUPABASE_DB_DSN,
    SUPABASE_KEY,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
)

logger = logging.getLogger("financial")

ollama_available: bool = False
resumen_available: bool = False
supabase: SupabaseClient | None = None
supabase_admin: SupabaseClient | None = None
embedding_model=None
twilio_client: TwilioClient | None = None

db_pool: psycopg2.pool.ThreadedConnectionPool | None = None
whatsapp_http_client: httpx.AsyncClient | None = None
resumen_client: httpx.AsyncClient | None = None


def twilio_configured() -> bool:
    """Indica si están presentes las credenciales mínimas de Twilio."""
    return all((TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))

def meta_whatsapp_configured() -> bool:
    """Indica si están presentes las credenciales mínimas para enviar mensajes."""
    return all(
        (
            META_WHATSAPP_TOKEN,
            META_PHONE_NUMBER_ID,
            META_GRAPH_API_VERSION,
        )
    )

def resumen_configured() -> bool:
    """Indica si están presentes las credenciales del modelo Groq de resúmenes."""
    return all((RES_URL, RES_MODEL, RES_KEY))


 
 
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa y cierra las dependencias compartidas de la aplicación."""
    global ollama_available, resumen_available, resumen_client
    global supabase, supabase_admin, embedding_model, db_pool
    global whatsapp_http_client, twilio_client

    if meta_whatsapp_configured():
        whatsapp_http_client = httpx.AsyncClient(timeout=30)
        logger.info("Meta WhatsApp Cloud API configurada")
    else:
        logger.warning(
            "Faltan credenciales de Meta WhatsApp; el envío de mensajes está deshabilitado"
        )

    if twilio_configured():
        try:
            twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            logger.info("Cliente de Twilio inicializado")
        except Exception as error:
            logger.error("No se pudo inicializar Twilio: %s", error)
            twilio_client = None
    else:
        logger.warning("Twilio no está configurado; el modo DEBUG con Twilio no funcionará")

    if OLLAMA_URL and "groq.com" in OLLAMA_URL.lower():
        ollama_available = True
        logger.info("Groq Cloud configurado - modelo: %s", OLLAMA_MODEL)
    else:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{OLLAMA_URL}/api/tags", timeout=5)
                if response.status_code == 200:
                    models = [model["name"] for model in response.json().get("models", [])]
                    if any(OLLAMA_MODEL in model for model in models):
                        ollama_available = True
                        logger.info("Ollama conectado - modelo: %s", OLLAMA_MODEL)
                    else:
                        logger.warning(
                            "Ollama está activo, pero no contiene %s. Disponibles: %s",
                            OLLAMA_MODEL,
                            models,
                        )
                else:
                    logger.warning(
                        "El servidor de IA respondió con código %s",
                        response.status_code,
                    )
        except Exception:
            logger.warning("⚠️ Ollama not running - AI chat disabled.")

    if resumen_configured():
        try:
            resumen_client = httpx.AsyncClient(
                base_url=RES_URL,
                headers={"Authorization": f"Bearer {RES_KEY}"},
                timeout=30,
            )
            response = await resumen_client.get("/models")
            if response.status_code == 200:
                resumen_available = True
                logger.info(
                    "Modelo de resúmenes Groq conectado - modelo: %s", RES_MODEL
                )
            else:
                logger.warning(
                    "Groq respondió con código %s al verificar el modelo de resúmenes",
                    response.status_code,
                )
        except Exception as error:
            logger.error(
                "No se pudo conectar al modelo de resúmenes Groq: %s", error
            )
            resumen_available = False
    else:
        logger.warning(
            "RES_URL/RES_MODEL/RES_KEY no configurados; "
            "los resúmenes con Groq quedan deshabilitados"
        )
 
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
            logger.info("Cliente de Supabase inicializado")
        except Exception as error:
            logger.error("No se pudo inicializar Supabase: %s", error)
    else:
        logger.warning("Supabase no está configurado; se usará almacenamiento en memoria")

    if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
        try:
            supabase_admin = create_client(
                SUPABASE_URL,
                SUPABASE_SERVICE_ROLE_KEY,
            )
            logger.info("Cliente administrativo de Supabase inicializado")
        except Exception as error:
            logger.error(
                "No se pudo inicializar Supabase administrativo: %s",
                error,
            )
    else:
        logger.warning(
            "SUPABASE_SERVICE_ROLE_KEY no configurada; "
            "los recordatorios automáticos quedan deshabilitados"
        )

    if SUPABASE_DB_DSN:
        try:

            logger.info("🔌 Creando pool de conexiones Postgres para RAG...")
            db_pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=1,
                maxconn=5,
                dsn=SUPABASE_DB_DSN,
            )
            logger.info("Pool de conexiones Postgres listo")
        except Exception as error:
            logger.error("No se pudo crear el pool de Postgres: %s", error)
            db_pool = None
    else:
        logger.warning("DB_DSN no configurado; la búsqueda RAG queda deshabilitada")

    yield

    if whatsapp_http_client is not None:
        await whatsapp_http_client.aclose()
        whatsapp_http_client = None

    if resumen_client is not None:
        await resumen_client.aclose()
        resumen_client = None

    if db_pool is not None:
        db_pool.closeall()
        db_pool = None
        logger.info("Pool de conexiones Postgres cerrado")

    supabase_admin = None