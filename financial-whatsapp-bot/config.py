import os
from dotenv import load_dotenv

load_dotenv()


# If using twilio
DEBUG= os.getenv("DEBUG", "False").lower() == "true"
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")

WHATSAPP_PROVIDER = os.getenv("WHATSAPP_PROVIDER", "meta").lower().strip()
META_WHATSAPP_TOKEN = os.getenv("META_WHATSAPP_TOKEN")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
META_WABA_ID = os.getenv("META_WABA_ID")
META_WEBHOOK_VERIFY_TOKEN = os.getenv("META_WEBHOOK_VERIFY_TOKEN")
META_APP_SECRET = os.getenv("META_APP_SECRET")
META_GRAPH_API_VERSION = os.getenv("META_GRAPH_API_VERSION", "").strip().strip("/")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
IA_API_KEY = os.getenv("IA_API_KEY", "")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
DB_DSN = os.getenv("DB_DSN")
EMBEDDING_MODEL_NAME = "intfloat/multilingual-e5-base"
SUPABASE_DB_DSN = os.getenv("DB_DSN")
DB_DSN = SUPABASE_DB_DSN

RES_URL=os.getenv("RES_URL")
RES_MODEL=os.getenv("RES_MODEL")
RES_KEY=os.getenv("RES_KEY")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


HF_TOKEN= os.getenv("HF_TOKEN")
MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "intfloat/multilingual-e5-base")


REMINDERS_ENABLED = (
    os.getenv("REMINDERS_ENABLED", "false").strip().lower() == "true"
)
REMINDER_TEMPLATE_NAME = os.getenv(
    "REMINDER_TEMPLATE_NAME",
    "recordatorio_roadmap",
).strip()
REMINDER_FINAL_TEMPLATE_NAME = os.getenv(
    "REMINDER_FINAL_TEMPLATE_NAME",
    REMINDER_TEMPLATE_NAME,
).strip()
REMINDER_TEMPLATE_LANGUAGE = os.getenv(
    "REMINDER_TEMPLATE_LANGUAGE",
    "es_CL",
).strip()
REMINDER_RECIPIENT_LABEL = os.getenv(
    "REMINDER_RECIPIENT_LABEL",
    "emprendedor/a",
).strip()
REMINDER_DAYS = int(os.getenv("REMINDER_DAYS", "3"))
REMINDER_TIMEZONE = os.getenv(
    "REMINDER_TIMEZONE",
    "America/Santiago",
).strip()
REMINDER_BATCH_SIZE = int(os.getenv("REMINDER_BATCH_SIZE", "100"))
# Vercel envía CRON_SECRET como `Authorization: Bearer ...` al ejecutar el cron.
# El nombre anterior se mantiene solo como respaldo para entornos locales ya creados.
CRON_SECRET = (
    os.getenv("CRON_SECRET")
    or os.getenv("REMINDER_CRON_SECRET", "")
).strip()

# Límite distribuido de mensajes entrantes por número telefónico.
RATE_LIMIT_ENABLED = (
    os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower() == "true"
)
RATE_LIMIT_MAX_MESSAGES = int(os.getenv("RATE_LIMIT_MAX_MESSAGES", "2"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
RATE_LIMIT_BLOCK_SECONDS = int(os.getenv("RATE_LIMIT_BLOCK_SECONDS", "60"))

# Similitud mínima (1 - distancia coseno de pgvector) para que un chunk del RAG
# se considere lo bastante relevante como para llegar al prompt del LLM.
RAG_SIMILARITY_THRESHOLD = float(os.getenv("RAG_SIMILARITY_THRESHOLD", "0.6"))

# Cuentas del panel municipal (InnovaRecoleta, El Bosque). Hardcodeadas a
# propósito: solo hay 2 municipalidades clientes por ahora, no se justifica
# un sistema de registro/roles todavía. Las contraseñas viven acá solo como
# nombre de variable de entorno — el valor real nunca va en el código.
ADMIN_ACCOUNTS = {
    "recoleta": {
        "password": os.getenv("ADMIN_RECOLETA_PASSWORD", ""),
        "comuna": "Recoleta",
        "nombre": "InnovaRecoleta",
    },
    "elbosque": {
        "password": os.getenv("ADMIN_ELBOSQUE_PASSWORD", ""),
        "comuna": "El Bosque",
        "nombre": "Municipalidad de El Bosque",
    },
}

if REMINDER_DAYS < 1:
    raise ValueError("REMINDER_DAYS debe ser mayor que cero")

if REMINDER_BATCH_SIZE < 1:
    raise ValueError("REMINDER_BATCH_SIZE debe ser mayor que cero")

if RATE_LIMIT_MAX_MESSAGES < 1:
    raise ValueError("RATE_LIMIT_MAX_MESSAGES debe ser mayor que cero")

if RATE_LIMIT_WINDOW_SECONDS < 1:
    raise ValueError("RATE_LIMIT_WINDOW_SECONDS debe ser mayor que cero")

if RATE_LIMIT_BLOCK_SECONDS < 1:
    raise ValueError("RATE_LIMIT_BLOCK_SECONDS debe ser mayor que cero")

if not 0.0 <= RAG_SIMILARITY_THRESHOLD <= 1.0:
    raise ValueError("RAG_SIMILARITY_THRESHOLD debe estar entre 0.0 y 1.0")
