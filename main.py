"""
FinancIAl - WhatsApp Bot MVP
Backend FastAPI + Twilio + Ollama (Llama 3.2)
"""

import os
import json
import logging
import httpx
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, BackgroundTasks
from fastapi.responses import PlainTextResponse
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse
from supabase import create_client, Client as SupabaseClient
from dotenv import load_dotenv
load_dotenv()

# ── Logging ──
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("financial")

# ── Config ──
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")  # Sandbox default
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")  # Ollama local
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# ── Clients ──
twilio_client = None
ollama_available = False
supabase: SupabaseClient = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize clients on startup."""
    global twilio_client, ollama_available, supabase
    
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
        twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        logger.info("✅ Twilio client initialized")
    else:
        logger.warning("⚠️ Twilio credentials not set - running in test mode")
    
    # Check if Ollama is running
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
    except Exception:
        logger.warning("⚠️ Ollama not running - AI chat disabled. Start Ollama and restart the server.")

    if SUPABASE_URL and SUPABASE_KEY:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("✅ Supabase client initialized")
    else:
        logger.warning("⚠️ Supabase credentials not set - using in-memory storage")
    
    yield


app = FastAPI(title="FinancIAl WhatsApp Bot", lifespan=lifespan)

# ══════════════════════════════════════════════════════════════
# IN-MEMORY STORAGE (fallback when Supabase is not configured)
# Replace with Supabase calls in production
# ══════════════════════════════════════════════════════════════

users_db: dict = {}


def get_user(phone: str) -> dict | None:
    """Get user profile from storage."""
    if supabase:
        try:
            result = supabase.table("users").select("*").eq("phone", phone).execute()
            if result.data:
                user = result.data[0]
                # Parse JSON fields
                if isinstance(user.get("roadmap"), str):
                    user["roadmap"] = json.loads(user["roadmap"])
                if isinstance(user.get("conversation_history"), str):
                    user["conversation_history"] = json.loads(user["conversation_history"])
                return user
        except Exception as e:
            logger.error(f"Supabase get error: {e}")
    return users_db.get(phone)


def save_user(phone: str, data: dict):
    """Save user profile to storage."""
    data["phone"] = phone
    data["updated_at"] = datetime.utcnow().isoformat()
    
    if supabase:
        try:
            # Serialize complex fields
            db_data = {**data}
            if "roadmap" in db_data:
                db_data["roadmap"] = json.dumps(db_data["roadmap"])
            if "conversation_history" in db_data:
                db_data["conversation_history"] = json.dumps(db_data["conversation_history"])
            
            supabase.table("users").upsert(db_data, on_conflict="phone").execute()
            return
        except Exception as e:
            logger.error(f"Supabase save error: {e}")
    
    users_db[phone] = data


# ══════════════════════════════════════════════════════════════
# ROADMAPS POR RUBRO
# ══════════════════════════════════════════════════════════════

ROADMAPS = {
    "textil": [
        {"id": 1, "title": "Obtener Cédula de Identidad vigente", "desc": "Necesitas tu CI vigente para todos los trámites. Si está vencida, renuévala en el Registro Civil.", "done": False},
        {"id": 2, "title": "Obtener RUT en el SII", "desc": "Si no tienes RUT, inscríbete en sii.cl o en oficina del SII. Es gratis y en el día.", "done": False},
        {"id": 3, "title": "Inicio de Actividades en el SII", "desc": "Entra a sii.cl → 'Inicio de actividades'. Elige la categoría textil/confección.", "done": False},
        {"id": 4, "title": "Solicitar Patente Municipal", "desc": "Ve a tu municipalidad con el inicio de actividades y solicita la patente comercial.", "done": False},
        {"id": 5, "title": "Resolución Sanitaria (si aplica)", "desc": "Si trabajas con telas que requieren tratamiento especial, podrías necesitar resolución SEREMI.", "done": False},
        {"id": 6, "title": "Emitir tu primera boleta", "desc": "¡Ya puedes facturar! Entra al SII y emite boletas electrónicas.", "done": False},
    ],
    "alimentos": [
        {"id": 1, "title": "Obtener Cédula de Identidad vigente", "desc": "Tu CI vigente es necesaria para todo el proceso.", "done": False},
        {"id": 2, "title": "Obtener RUT en el SII", "desc": "Inscríbete en sii.cl si no tienes RUT. Gratis y en el día.", "done": False},
        {"id": 3, "title": "Inicio de Actividades en el SII", "desc": "Entra a sii.cl y selecciona la categoría de alimentos.", "done": False},
        {"id": 4, "title": "Resolución Sanitaria SEREMI", "desc": "OBLIGATORIO para alimentos. Solicita autorización en la SEREMI de Salud. Necesitas informe de condiciones de tu cocina/taller.", "done": False},
        {"id": 5, "title": "Autorización SAG (si aplica)", "desc": "Si vendes productos de origen animal (snacks mascotas, lácteos, etc.), necesitas permiso del SAG.", "done": False},
        {"id": 6, "title": "Solicitar Patente Municipal", "desc": "Con inicio de actividades y resolución sanitaria, solicita la patente en tu municipalidad.", "done": False},
        {"id": 7, "title": "Emitir tu primera boleta", "desc": "¡Listo! Ya puedes emitir boletas electrónicas desde el SII.", "done": False},
    ],
    "joyeria": [
        {"id": 1, "title": "Obtener Cédula de Identidad vigente", "desc": "Tu CI vigente es necesaria para todo el proceso.", "done": False},
        {"id": 2, "title": "Obtener RUT en el SII", "desc": "Inscríbete en sii.cl si no tienes RUT.", "done": False},
        {"id": 3, "title": "Inicio de Actividades en el SII", "desc": "Entra a sii.cl y elige la categoría artesanía/joyería.", "done": False},
        {"id": 4, "title": "Solicitar Patente Municipal", "desc": "Ve a la municipalidad con tu inicio de actividades.", "done": False},
        {"id": 5, "title": "Emitir tu primera boleta", "desc": "¡Ya puedes facturar oficialmente!", "done": False},
    ],
    "otro": [
        {"id": 1, "title": "Obtener Cédula de Identidad vigente", "desc": "Tu CI vigente es el primer paso.", "done": False},
        {"id": 2, "title": "Obtener RUT en el SII", "desc": "Inscríbete en sii.cl.", "done": False},
        {"id": 3, "title": "Inicio de Actividades en el SII", "desc": "Entra a sii.cl → 'Inicio de actividades' y selecciona tu categoría.", "done": False},
        {"id": 4, "title": "Verificar permisos sectoriales", "desc": "Dependiendo de tu rubro, podrías necesitar permisos adicionales. Pregúntame y te oriento.", "done": False},
        {"id": 5, "title": "Solicitar Patente Municipal", "desc": "Ve a tu municipalidad con el inicio de actividades.", "done": False},
        {"id": 6, "title": "Emitir tu primera boleta", "desc": "¡Ya puedes facturar!", "done": False},
    ],
    "formalizado": [
        {"id": 1, "title": "✅ Ya estás formalizado", "desc": "Tu negocio ya tiene inicio de actividades. Ahora enfócate en crecer.", "done": True},
        {"id": 2, "title": "Revisar obligaciones tributarias", "desc": "Verifica que estés al día con declaraciones mensuales (F29) y anuales.", "done": False},
        {"id": 3, "title": "Explorar fondos concursables", "desc": "Revisa si calificas para Capital Semilla, Capital Abeja, CORFO u otros.", "done": False},
        {"id": 4, "title": "Optimizar tu negocio", "desc": "Pregúntame sobre métricas, precios, costos o estrategias para tu rubro.", "done": False},
    ],
}


# ══════════════════════════════════════════════════════════════
# FONDOS CONCURSABLES
# ══════════════════════════════════════════════════════════════

def simulate_funds(user: dict) -> str:
    """Generate fund simulation based on user profile."""
    is_formal = user.get("inicio_sii") == "si"
    rubro = user.get("rubro", "otro")
    
    funds = [
        {
            "name": "💰 Capital Semilla - SERCOTEC",
            "reqs": [
                ("Persona natural mayor de 18 años", True),
                ("Inicio de actividades en SII", is_formal),
                ("Antigüedad menor a 2 años", True),
                ("Ventas menores a 2.400 UF/año", True),
                ("Capacitación en gestión empresarial", False),
            ]
        },
        {
            "name": "🐝 Capital Abeja - SERCOTEC",
            "reqs": [
                ("Mujer emprendedora", None),
                ("Mayor de 18 años", True),
                ("Inicio de actividades en SII", is_formal),
                ("Ventas menores a 5.000 UF/año", True),
            ]
        },
        {
            "name": "📈 Crece - SERCOTEC",
            "reqs": [
                ("Inicio de actividades > 6 meses", is_formal if is_formal else False),
                ("Ventas entre 200 y 5.000 UF/año", None),
                ("Patente municipal al día", None),
            ]
        },
    ]
    
    lines = ["🎯 *Simulación de Fondos Concursables*\n"]
    lines.append(f"Basado en tu perfil: _{rubro}_ | {'Formalizado' if is_formal else 'No formalizado'}\n")
    
    for fund in funds:
        met = sum(1 for _, v in fund["reqs"] if v is True)
        total = len(fund["reqs"])
        pct = round((met / total) * 100)
        
        lines.append(f"\n*{fund['name']}*")
        lines.append(f"Compatibilidad: {pct}%")
        
        for req_text, req_met in fund["reqs"]:
            if req_met is True:
                lines.append(f"  ✅ {req_text}")
            elif req_met is False:
                lines.append(f"  ❌ {req_text}")
            else:
                lines.append(f"  ⚠️ {req_text} _(necesito más info)_")
    
    lines.append("\n💡 *¿Quieres que te ayude a cumplir los requisitos que te faltan?* Escribe el nombre del fondo que te interesa.")
    
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# SYSTEM PROMPT PARA IA
# ══════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """Eres FinancIAl, un asistente virtual de WhatsApp especializado en ayudar a microemprendedores chilenos con:
1. Formalización de negocios (trámites SII, municipalidad, permisos)
2. Gestión financiera básica (costos, precios, márgenes)
3. Postulación a fondos concursables (SERCOTEC, CORFO, FOSIS)

REGLAS ESTRICTAS:
- Responde SIEMPRE en español chileno, cercano y simple
- Respuestas BREVES: máximo 3-4 oraciones. Esto es WhatsApp, no un ensayo
- NUNCA uses términos técnicos sin explicarlos con un ejemplo del rubro del usuario
- Si no sabes algo con certeza, dilo honestamente y sugiere consultar SII/municipalidad
- Si la pregunta está fuera de tu dominio (salud, legal complejo, etc.), indícalo y sugiere un profesional
- Usa emojis con moderación para ser amigable
- Formatea con *negritas* y _cursivas_ de WhatsApp cuando ayude a la claridad

CONTEXTO DEL USUARIO:
- Rubro: {rubro}
- Comuna: {comuna}
- Estado SII: {estado_sii}
- Progreso formalización: {progreso}

Responde considerando siempre este contexto sin que el usuario lo tenga que repetir."""


def get_ai_response(user: dict, message: str) -> str:
    """Get AI response from Ollama (Llama 3.2)."""
    if not ollama_available:
        return "⚠️ El asistente de IA no está disponible en este momento. Asegúrate de que Ollama esté corriendo."
    
    # Build context
    roadmap = user.get("roadmap", [])
    completed = sum(1 for h in roadmap if h.get("done"))
    total = len(roadmap)
    current_hito = next((h for h in roadmap if not h.get("done")), None)
    
    progreso = f"{completed}/{total} hitos completados"
    if current_hito:
        progreso += f". Siguiente: {current_hito['title']}"
    
    system = SYSTEM_PROMPT.format(
        rubro=user.get("rubro", "No definido"),
        comuna=user.get("comuna", "No definida"),
        estado_sii="Formalizado" if user.get("inicio_sii") == "si" else "No formalizado",
        progreso=progreso,
    )
    
    # Build conversation history (last 6 messages for smaller model)
    history = user.get("conversation_history", [])[-6:]
    messages = [{"role": "system", "content": system}]
    messages.extend(history)
    messages.append({"role": "user", "content": message})
    
    try:
        response = httpx.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": 0.7,
                    "num_predict": 400,  # Max tokens
                }
            },
            timeout=60,  # Ollama can be slower than cloud APIs
        )
        
        data = response.json()
        ai_text = data.get("message", {}).get("content", "")
        
        if not ai_text:
            return "😅 No pude generar una respuesta. ¿Puedes intentar de nuevo?"
        
        # Save to history
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": ai_text})
        user["conversation_history"] = history[-12:]  # Keep last 12
        
        return ai_text
    except httpx.TimeoutException:
        logger.error("Ollama timeout")
        return "⏳ Me demoré mucho pensando. ¿Puedes intentar con una pregunta más corta?"
    except Exception as e:
        logger.error(f"Ollama error: {e}")
        return "😅 Tuve un problema al procesar tu consulta. ¿Puedes intentar de nuevo?"


# ══════════════════════════════════════════════════════════════
# ONBOARDING FLOW
# ══════════════════════════════════════════════════════════════

RUBRO_KEYWORDS = {
    "textil": ["textil", "ropa", "confección", "confeccion", "costura", "tela", "lenceria", "lencería", "jeans", "polera"],
    "alimentos": ["alimento", "comida", "cocina", "gastronomía", "gastronomia", "snack", "dulce", "chocolate", "pastel", "torta", "pan", "empanada", "cocinar"],
    "joyeria": ["joya", "joyería", "joyeria", "plata", "anillo", "collar", "pulsera", "artesanía", "artesania", "bisutería", "bisuteria", "febrería", "febreria"],
}


def detect_rubro(text: str) -> str:
    """Detect rubro from free text."""
    text_lower = text.lower().strip()
    for rubro, keywords in RUBRO_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return rubro
    return "otro"


def detect_sii(text: str) -> str | None:
    """Detect SII status from free text."""
    text_lower = text.lower().strip()
    positive = ["si", "sí", "ya", "listo", "hecho", "tengo", "formalizado", "formalizada"]
    negative = ["no", "todavía", "todavia", "aún", "aun", "nada", "nunca"]
    unknown = ["no sé", "no se", "qué es", "que es"]
    
    for kw in unknown:
        if kw in text_lower:
            return "no_sabe"
    for kw in positive:
        if kw in text_lower:
            return "si"
    for kw in negative:
        if kw in text_lower:
            return "no"
    return None


def process_onboarding(user: dict, message: str) -> str:
    """Handle onboarding flow step by step."""
    step = user.get("onboarding_step", 0)
    
    # Step 0: Welcome
    if step == 0:
        user["onboarding_step"] = 1
        save_user(user["phone"], user)
        return (
            "¡Hola! 👋 Soy *FinancIAl*, tu asistente para formalizar y hacer crecer tu emprendimiento.\n\n"
            "Voy a hacerte *3 preguntas rápidas* para personalizar tu experiencia.\n\n"
            "📌 *Pregunta 1 de 3:*\n"
            "¿En qué rubro está tu emprendimiento?\n\n"
            "Ejemplo: _textil, alimentos, joyería, etc._"
        )
    
    # Step 1: Rubro
    if step == 1:
        rubro = detect_rubro(message)
        user["rubro"] = rubro
        user["rubro_raw"] = message.strip()
        user["onboarding_step"] = 2
        save_user(user["phone"], user)
        
        rubro_display = user["rubro_raw"] if rubro == "otro" else rubro.capitalize()
        return (
            f"✅ Rubro: *{rubro_display}*\n\n"
            "📍 *Pregunta 2 de 3:*\n"
            "¿En qué comuna trabajas?\n\n"
            "Ejemplo: _Recoleta, El Bosque, Santiago, etc._"
        )
    
    # Step 2: Comuna
    if step == 2:
        user["comuna"] = message.strip().title()
        user["onboarding_step"] = 3
        save_user(user["phone"], user)
        return (
            f"✅ Comuna: *{user['comuna']}*\n\n"
            "📋 *Pregunta 3 de 3:*\n"
            "¿Ya hiciste tu inicio de actividades en el SII?\n\n"
            "Responde: _sí_, _no_, o _no sé qué es eso_"
        )
    
    # Step 3: SII Status
    if step == 3:
        sii = detect_sii(message)
        if sii is None:
            return "🤔 No entendí bien. ¿Ya tienes inicio de actividades en el SII?\n\nResponde: _sí_, _no_, o _no sé qué es eso_"
        
        user["inicio_sii"] = sii if sii != "no_sabe" else "no"
        user["onboarding_step"] = "done"
        
        # Generate roadmap
        if sii == "si":
            roadmap_key = "formalizado"
        else:
            roadmap_key = user.get("rubro", "otro")
        
        import copy
        user["roadmap"] = copy.deepcopy(ROADMAPS.get(roadmap_key, ROADMAPS["otro"]))
        user["conversation_history"] = []
        user["created_at"] = datetime.utcnow().isoformat()
        save_user(user["phone"], user)
        
        total = len(user["roadmap"])
        rubro_display = user.get("rubro_raw", user.get("rubro", "")).capitalize()
        estado = "Formalizado ✅" if sii == "si" else "No formalizado"
        
        sii_explain = ""
        if sii == "no_sabe":
            sii_explain = "\n\n💡 _El inicio de actividades es el trámite que registra tu negocio ante el SII. Sin esto, no puedes emitir boletas ni facturas. ¡Pero no te preocupes, te voy a guiar paso a paso!_\n"
        
        return (
            f"🎉 *¡Perfecto! Ya tengo tu perfil:*\n\n"
            f"📌 Rubro: *{rubro_display}*\n"
            f"📍 Comuna: *{user['comuna']}*\n"
            f"📋 Estado SII: *{estado}*\n"
            f"{sii_explain}\n"
            f"Te preparé un *roadmap personalizado* con *{total} hitos* para "
            f"{'hacer crecer' if sii == 'si' else 'formalizar'} tu negocio.\n\n"
            "¿Qué quieres hacer ahora? Escribe:\n\n"
            "📋 *\"mi roadmap\"* → ver tus pasos\n"
            "🎯 *\"postular a fondo\"* → simular postulación\n"
            "💬 O simplemente *hazme cualquier pregunta* sobre tu negocio"
        )
    
    return None


# ══════════════════════════════════════════════════════════════
# ROADMAP HANDLER
# ══════════════════════════════════════════════════════════════

def get_roadmap_text(user: dict) -> str:
    """Generate roadmap status message."""
    roadmap = user.get("roadmap", [])
    if not roadmap:
        return "⚠️ No tienes un roadmap generado. Escribe *hola* para empezar."
    
    completed = sum(1 for h in roadmap if h.get("done"))
    total = len(roadmap)
    pct = round((completed / total) * 100)
    
    # Progress bar
    filled = round(pct / 10)
    bar = "🟩" * filled + "⬜" * (10 - filled)
    
    lines = [
        f"📋 *Tu Roadmap de Formalización*\n",
        f"{bar} {pct}%",
        f"_{completed} de {total} hitos completados_\n",
    ]
    
    for h in roadmap:
        status = "✅" if h["done"] else "⬜"
        lines.append(f"{status} *{h['title']}*")
        if not h["done"]:
            lines.append(f"   ↳ _{h['desc']}_\n")
    
    next_hito = next((h for h in roadmap if not h.get("done")), None)
    if next_hito:
        lines.append(f"\n👉 *Tu siguiente paso:* {next_hito['title']}")
        lines.append(f"\nEscribe *\"listo\"* cuando completes este hito, o *\"ayuda\"* si necesitas orientación.")
    else:
        lines.append("\n🎉 *¡Completaste todos los hitos!* Tu negocio está formalizado.")
        lines.append("\nEscribe *\"postular a fondo\"* para explorar financiamiento.")
    
    return "\n".join(lines)


def mark_hito_done(user: dict) -> str:
    """Mark current hito as done and show next."""
    roadmap = user.get("roadmap", [])
    current = next((h for h in roadmap if not h.get("done")), None)
    
    if not current:
        return "🎉 ¡Ya completaste todos los hitos! No hay más pendientes."
    
    current["done"] = True
    save_user(user["phone"], user)
    
    completed = sum(1 for h in roadmap if h["done"])
    total = len(roadmap)
    pct = round((completed / total) * 100)
    
    next_hito = next((h for h in roadmap if not h.get("done")), None)
    
    if next_hito:
        return (
            f"✅ ¡Bien! Completaste: *{current['title']}*\n\n"
            f"📊 Progreso: {pct}% ({completed}/{total})\n\n"
            f"👉 *Tu siguiente paso:*\n"
            f"*{next_hito['title']}*\n"
            f"_{next_hito['desc']}_\n\n"
            f"Escribe *\"listo\"* al completarlo, o *\"ayuda\"* si necesitas orientación."
        )
    else:
        return (
            f"✅ ¡Completaste: *{current['title']}*\n\n"
            f"🎉🎉🎉 *¡FELICITACIONES!* 🎉🎉🎉\n\n"
            f"Completaste el 100% de tu roadmap. ¡Tu negocio está formalizado!\n\n"
            f"¿Qué sigue?\n"
            f"🎯 Escribe *\"postular a fondo\"* para buscar financiamiento\n"
            f"💬 O hazme cualquier pregunta sobre cómo hacer crecer tu negocio"
        )


# ══════════════════════════════════════════════════════════════
# MAIN MESSAGE ROUTER
# ══════════════════════════════════════════════════════════════

def route_message(phone: str, message: str) -> str:
    """Main router: determines what to do with each incoming message."""
    message = message.strip()
    msg_lower = message.lower()
    
    # Get or create user
    user = get_user(phone)
    
    if not user:
        user = {"phone": phone, "onboarding_step": 0}
        save_user(phone, user)
    
    # ── Onboarding flow ──
    if user.get("onboarding_step") != "done":
        response = process_onboarding(user, message)
        if response:
            return response
    
    # ── Reset command ──
    if msg_lower in ["reiniciar", "reset", "empezar de nuevo"]:
        users_db.pop(phone, None)
        if supabase:
            try:
                supabase.table("users").delete().eq("phone", phone).execute()
            except:
                pass
        new_user = {"phone": phone, "onboarding_step": 0}
        save_user(phone, new_user)
        return process_onboarding(new_user, message)
    
    # ── Roadmap commands ──
    roadmap_triggers = ["roadmap", "mi roadmap", "hitos", "qué me falta", "que me falta", "formalizar", "mis pasos", "mi ruta"]
    if any(trigger in msg_lower for trigger in roadmap_triggers):
        return get_roadmap_text(user)
    
    # ── Mark hito done ──
    done_triggers = ["listo", "hecho", "completado", "ya lo hice", "ya está", "ya esta", "siguiente"]
    if any(trigger in msg_lower for trigger in done_triggers):
        return mark_hito_done(user)
    
    # ── Fund simulation ──
    fund_triggers = ["fondo", "postular", "capital semilla", "capital abeja", "sercotec", "corfo", "financiamiento"]
    if any(trigger in msg_lower for trigger in fund_triggers):
        return simulate_funds(user)
    
    # ── Help ──
    if msg_lower in ["ayuda", "help", "menu", "menú", "opciones"]:
        return (
            "📱 *Menú de FinancIAl*\n\n"
            "Escribe cualquiera de estas opciones:\n\n"
            "📋 *\"mi roadmap\"* → ver tu progreso de formalización\n"
            "✅ *\"listo\"* → marcar el hito actual como completado\n"
            "🎯 *\"postular a fondo\"* → simular postulación a fondos\n"
            "❓ *\"ayuda\"* → ver este menú\n"
            "🔄 *\"reiniciar\"* → empezar de nuevo\n\n"
            "💬 O simplemente *escribe tu pregunta* y te respondo con IA 🤖"
        )
    
    # ── AI Chat (default) ──
    # Return special flag - AI will be processed in background
    return "__AI_QUERY__"


def process_ai_and_send(phone_whatsapp: str, phone_clean: str, message: str):
    """Process AI query and send response via Twilio (runs in background)."""
    user = get_user(phone_clean)
    if not user:
        return
    
    ai_response = get_ai_response(user, message)
    save_user(phone_clean, user)
    
    # Send via Twilio
    if twilio_client:
        try:
            twilio_client.messages.create(
                body=ai_response,
                from_=TWILIO_WHATSAPP_NUMBER,
                to=phone_whatsapp,
            )
            logger.info(f"📤 AI Response sent to {phone_whatsapp}: {ai_response[:100]}...")
        except Exception as e:
            logger.error(f"Twilio send error: {e}")
    else:
        logger.info(f"📤 AI Response (no Twilio): {ai_response[:100]}...")


# ══════════════════════════════════════════════════════════════
# TWILIO WEBHOOK
# ══════════════════════════════════════════════════════════════

@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle incoming WhatsApp messages from Twilio."""
    form = await request.form()
    
    phone = form.get("From", "")  # e.g., "whatsapp:+56912345678"
    message = form.get("Body", "").strip()
    
    logger.info(f"📩 Message from {phone}: {message}")
    
    # Clean phone number
    phone_clean = phone.replace("whatsapp:", "").strip()
    
    # Route and get response
    response_text = route_message(phone_clean, message)
    
    # Build TwiML response
    twiml = MessagingResponse()
    
    if response_text == "__AI_QUERY__":
        # AI queries: respond immediately with "thinking" message, process in background
        twiml.message("🤔 Déjame pensar tu respuesta...")
        background_tasks.add_task(process_ai_and_send, phone, phone_clean, message)
    else:
        logger.info(f"📤 Response to {phone}: {response_text[:100]}...")
        if len(response_text) > 4000:
            parts = split_message(response_text, 4000)
            for part in parts:
                twiml.message(part)
        else:
            twiml.message(response_text)
    
    return Response(content=str(twiml), media_type="application/xml")


def split_message(text: str, max_len: int) -> list[str]:
    """Split long message into chunks at paragraph boundaries."""
    if len(text) <= max_len:
        return [text]
    
    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        
        # Find last newline before limit
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        
        parts.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    
    return parts


# ══════════════════════════════════════════════════════════════
# TEST ENDPOINT (for development without Twilio)
# ══════════════════════════════════════════════════════════════

@app.post("/test/chat")
async def test_chat(request: Request):
    """Test endpoint - simulates WhatsApp without Twilio."""
    data = await request.json()
    phone = data.get("phone", "+56900000000")
    message = data.get("message", "")
    
    response = route_message(phone, message)
    return {"response": response, "phone": phone}


@app.get("/test/chat")
async def test_chat_ui():
    """Simple HTML UI for testing without WhatsApp."""
    return Response(content="""
    <!DOCTYPE html>
    <html>
    <head>
        <title>FinancIAl - Test Chat</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * { margin:0; padding:0; box-sizing:border-box; }
            body { font-family:system-ui; background:#0b141a; color:#e9edef; height:100vh; display:flex; flex-direction:column; max-width:500px; margin:0 auto; }
            .header { background:#202c33; padding:16px; text-align:center; border-bottom:1px solid #2a3942; }
            .header h1 { font-size:18px; color:#00a884; }
            .header p { font-size:12px; color:#8696a0; margin-top:4px; }
            .chat { flex:1; overflow-y:auto; padding:16px; }
            .msg { margin-bottom:12px; max-width:85%; padding:8px 12px; border-radius:8px; font-size:14px; line-height:1.5; white-space:pre-wrap; word-wrap:break-word; }
            .bot { background:#202c33; border-radius:0 8px 8px 8px; margin-right:auto; }
            .user { background:#005c4b; border-radius:8px 0 8px 8px; margin-left:auto; }
            .input-area { background:#202c33; padding:12px; display:flex; gap:8px; }
            input { flex:1; background:#2a3942; border:none; border-radius:24px; padding:10px 16px; color:#e9edef; font-size:15px; outline:none; }
            button { background:#00a884; border:none; border-radius:50%; width:42px; height:42px; color:#111b21; font-size:18px; cursor:pointer; }
            button:hover { background:#25d366; }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>💰 FinancIAl - Test Mode</h1>
            <p>Simula conversación WhatsApp sin Twilio</p>
        </div>
        <div class="chat" id="chat"></div>
        <div class="input-area">
            <input id="input" placeholder="Escribe tu mensaje..." onkeydown="if(event.key==='Enter')send()">
            <button onclick="send()">➤</button>
        </div>
        <script>
            const chat = document.getElementById('chat');
            const input = document.getElementById('input');
            const phone = '+569' + Math.floor(Math.random()*90000000+10000000);
            
            function addMsg(text, cls) {
                const div = document.createElement('div');
                div.className = 'msg ' + cls;
                div.textContent = text;
                chat.appendChild(div);
                chat.scrollTop = chat.scrollHeight;
            }
            
            async function send() {
                const msg = input.value.trim();
                if (!msg) return;
                addMsg(msg, 'user');
                input.value = '';
                
                try {
                    const res = await fetch('/test/chat', {
                        method: 'POST',
                        headers: {'Content-Type':'application/json'},
                        body: JSON.stringify({phone, message: msg})
                    });
                    const data = await res.json();
                    addMsg(data.response, 'bot');
                } catch(e) {
                    addMsg('Error de conexión', 'bot');
                }
            }
            
            // Auto-start
            fetch('/test/chat', {
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body: JSON.stringify({phone, message:'hola'})
            }).then(r=>r.json()).then(d=>addMsg(d.response,'bot'));
        </script>
    </body>
    </html>
    """, media_type="text/html")


# ══════════════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════════════

@app.get("/")
async def health():
    return {
        "status": "running",
        "service": "FinancIAl WhatsApp Bot",
        "version": "1.0.0-mvp",
        "twilio": "connected" if twilio_client else "not configured",
        "ollama": f"connected ({OLLAMA_MODEL})" if ollama_available else "not running",
        "supabase": "connected" if supabase else "in-memory mode",
    }