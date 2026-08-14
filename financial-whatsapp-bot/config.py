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
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DB_DSN = os.getenv("DB_DSN")
EMBEDDING_MODEL_NAME = "intfloat/multilingual-e5-base"
SUPABASE_DB_DSN = os.getenv("DB_DSN")
DB_DSN = SUPABASE_DB_DSN

HF_TOKEN= os.getenv("HF_TOKEN")
MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "intfloat/multilingual-e5-base")

