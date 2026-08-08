import logging
import os
import threading

import httpx
import psycopg2

from config import OLLAMA_URL, OLLAMA_MODEL, TWILIO_WHATSAPP_NUMBER, DB_DSN, MODEL_NAME,HF_TOKEN
from db.users import save_message, get_messages, contar_mensajes
import dependencies

logger = logging.getLogger("financial")


def configure_ollama_endpoint(ollama_url: str, ollama_model: str, ia_api_key: str):
    """Configura dinámicamente el endpoint de Ollama/Groq Cloud según la URL y la presencia de la API Key."""
    headers = {
        "Content-Type": "application/json",
    }

    if ia_api_key:
        # ☁️ MODO NUBE (Groq Cloud)
        base_url = ollama_url.rstrip("/")
        headers["Authorization"] = f"Bearer {ia_api_key}"
        for sufijo in ["/openai/v1", "/v1"]:
            if base_url.endswith(sufijo):
                base_url = base_url[:-len(sufijo)]
        endpoint_url = f"{base_url}/openai/v1/chat/completions"
    else:
        # 🚇 MODO TUNEL LOCAL (Ngrok de tu compañero)
        headers["ngrok-skip-browser-warning"] = "true"
        if "/v1" not in ollama_url:
            endpoint_url = f"{ollama_url.rstrip('/')}/v1/chat/completions"
        else:
            endpoint_url = f"{ollama_url.rstrip('/')}/chat/completions"

    return endpoint_url, headers


def llamar_llm(messages: list, max_tokens: int = 600, temperature: float = 0.2) -> str:
    """
    Llama al LLM (Groq Cloud u Ollama/Ngrok según configuración en .env).
    Recibe una lista de mensajes en formato OpenAI:
        [{"role": "system"/"user"/"assistant", "content": "..."}]
    """
    ollama_url = os.getenv("OLLAMA_URL", OLLAMA_URL)
    ollama_model = os.getenv("OLLAMA_MODEL", OLLAMA_MODEL)
    ia_api_key = os.getenv("IA_API_KEY", "")

    endpoint_url, headers = configure_ollama_endpoint(ollama_url, ollama_model, ia_api_key)

    logger.info(f"🚀 Despachando inferencia a: {endpoint_url}")
    try:
        response = httpx.post(
            endpoint_url,
            headers=headers,
            json={
                "model": ollama_model,
                "messages": messages,
                "stream": False,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "")

    except httpx.TimeoutException:
        logger.error("⏳ Timeout al conectar con el servidor de IA")
        return ""
    except Exception as e:
        logger.error(f"💥 Error crítico en llamar_llm: {e}")
        return ""





async def obtener_embedding_remoto(texto: str, prefix: str = "query") -> list[float]:
    """
    Genera el embedding usando la Inference API de Hugging Face de forma directa.
    Aplica el prefijo 'query:' o 'passage:' necesario para la familia multilingual-e5.
    """
    hf_token = HF_TOKEN
    model_name = MODEL_NAME 
    
    # URL del pipeline de extracción de características de Hugging Face
    url = f"https://api-inference.huggingface.co/pipeline/feature-extraction/{model_name}"
    
    headers = {
        "Content-Type": "application/json"
    }
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"

    # Formateo con el prefijo 'query:' / 'passage:' requerido por E5
    texto_con_prefijo = f"{prefix}: {texto}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            url,
            headers=headers,
            json={"inputs": texto_con_prefijo, "options": {"wait_for_model": True}}
        )
        response.raise_for_status()
        data = response.json()

        # Normalización de la respuesta de Hugging Face
        if isinstance(data, list) and len(data) > 0:
            # Si retorna una matriz 2D [[vector]], extraemos la primera fila
            if isinstance(data[0], list):
                return [float(x) for x in data[0]]
            return [float(x) for x in data]
        
        raise ValueError("Formato de respuesta inesperado desde Hugging Face Inference API")


def actualizar_resumen_conversacion(phone: str) -> str | None:
    """
    Genera un resumen de los últimos mensajes del usuario y lo guarda
    en users.resumen_conversacion. Devuelve el resumen generado, o None
    si no había mensajes o algo falló.
    """
    historial = get_messages(phone, limit=50)

    if not historial:
        logger.info(f"Sin historial para generar resumen: {phone}")
        return None

    conversacion_texto = "\n".join(
        f"{'Usuario' if m['role'] == 'user' else 'Asistente'}: {m['content']}"
        for m in historial
    )

    prompt_resumen = f"""Resume en máximo 150 palabras los puntos clave de esta
conversación sobre el proceso de formalización del usuario: qué dudas ha tenido,
qué trámites ha consultado, en qué comuna está, qué información NO se le pudo
entregar (para evitar repetir la misma búsqueda fallida). No repitas saludos
ni relleno conversacional.

Conversación:
{conversacion_texto}"""

    resumen = llamar_llm(
        [{"role": "user", "content": prompt_resumen}],
        max_tokens=200,
        temperature=0.2,
    )

    if not resumen:
        logger.error(f"llamar_llm devolvió vacío al generar resumen para {phone}")
        return None

    if dependencies.supabase:
        try:
            dependencies.supabase.table("users").update(
                {"resumen_conversacion": resumen}
            ).eq("phone", phone).execute()
            logger.info(f"✅ Resumen actualizado para {phone}")
        except Exception as e:
            logger.error(f"Supabase update resumen_conversacion error: {e}")
            return None

    return resumen


def actualizar_resumen_en_background(phone: str, background_tasks=None):
    """
    Dispara la actualización del resumen sin bloquear la respuesta al usuario.
    - Si se pasa un `background_tasks` de FastAPI (BackgroundTasks), lo usa.
    - Si no, lanza un hilo simple con threading.
    """
    if background_tasks is not None:
        background_tasks.add_task(actualizar_resumen_conversacion, phone)
    else:
        threading.Thread(
            target=actualizar_resumen_conversacion, args=(phone,), daemon=True
        ).start()


SYSTEM_PROMPT = """Eres FinancIAl, un asistente virtual de WhatsApp experto en guiar a microemprendedores chilenos en su proceso de formalización y crecimiento.

REGLAS DE COMPORTAMIENTO Y TONO:
- Responde SIEMPRE en español chileno, con un tono cercano, empático y muy simple.
- Tus respuestas deben ser BREVES y al grano: máximo 3-4 oraciones. Esto es WhatsApp, evita bloques densos de texto.
- NUNCA uses tecnicismos legales o tributarios a secas; explícalos siempre con un ejemplo cotidiano del rubro del usuario.
- Usa emojis con moderación para mantener la conversación amigable pero profesional.
- Formatea usando *negritas* para conceptos clave y _cursivas_ para ejemplos, respetando el formato de WhatsApp.

🧠 MEMORIA DE CONVERSACIONES ANTERIORES:
- El bloque [RESUMEN DE INTERACCIONES PREVIAS] resume lo que ya has hablado con este usuario en sesiones pasadas.
- Úsalo para no repetir preguntas ya respondidas y para dar continuidad natural (ej. si ya sabes que preguntó por su patente antes, no actúes como si fuera la primera vez).
- Si el resumen indica que ya intentaste ayudar con un tema y no tenías información disponible, no repitas la misma búsqueda fallida: reconócelo y ofrece una alternativa (ej. derivar a la municipalidad, o revisar si hay algo nuevo).
- Si dice "Sin historial previo relevante", trátalo como una conversación nueva, sin inventar contexto que no existe.

📋 REGLA ESTRICTA DE CONTROL RAG (PROHIBIDO INVENTAR):
- Actualmente solo manejas información municipal de DOS comunas: *Recoleta* y *El Bosque*.
- El usuario está registrado en *{comuna}*, pero puede preguntar por la OTRA comuna soportada explícitamente — en ese caso, responde con la información de la comuna que preguntó, aclarando brevemente que es de esa comuna y no la de su perfil (ej. "Para *El Bosque* esto funciona así...").
- Si el contexto viene marcado como "SIN COBERTURA", significa que el usuario preguntó por una comuna que no manejas. Responde: "Por ahora solo tengo información de *Recoleta* y *El Bosque*. Para tu comuna te recomiendo consultar directo en tu Municipalidad. 🏢"
- Si el contexto viene marcado como "SIN INFORMACIÓN", significa que sí cubrimos esa comuna pero no encontramos el dato específico. Responde: "Pucha, no manejo esa información específica para *{comuna}* en este momento. Te sugiero consultar directamente en el departamento de patentes de tu Municipalidad para ir a la segura. 🏢"
- Está TERMINANTEMENTE PROHIBIDO inventar plazos, departamentos, costos o requisitos que no estén explícitamente escritos en el contexto provisto. Si no está escrito, no lo digas.
- NUNCA mezcles información de Recoleta con la de El Bosque, aunque ambas estén disponibles: usa solo la comuna que corresponde a la pregunta.

CONTEXTO ACTUAL DEL EMPRENDEDOR:
- Rubro: {rubro}
- Comuna: {comuna}
- Estado SII: {estado_sii}
- Progreso en FinancIAl: {progreso}

Considera siempre este perfil para personalizar tu respuesta sin pedirle al usuario que se repita."""

async def obtener_contexto_rag(message: str, comuna_usuario: str) -> str:
    """
    Devuelve el contexto RAG para la comuna correcta según el router de detección.
    Si la comuna detectada no está soportada, no consulta la BD (ahorra una query)
    y devuelve directamente el mensaje de sin cobertura.
    """
    deteccion = detectar_comuna(message, comuna_usuario)
    comuna_busqueda = deteccion["comuna"]
 
    if not deteccion["soportada"]:
        return f"SIN COBERTURA: no manejamos información de la comuna '{comuna_busqueda}'. Solo Recoleta y El Bosque están disponibles."
 
    contexto = ""
    try:
        query_vector = await obtener_embedding_remoto(message)
 
        conn = psycopg2.connect(DB_DSN)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT content, metadata
                FROM documents
                WHERE metadata->>'comuna' ILIKE %s OR metadata->>'comuna' ILIKE '%%general%%'
                ORDER BY embedding <=> %s::vector
                LIMIT 4;
            """, (f"%{comuna_busqueda}%", query_vector))
 
            resultados = cur.fetchall()
        conn.close()
 
        if resultados:
            for res in resultados:
                meta = res[1] if res[1] else {}
                file_name = meta.get("file_name", "Municipal")
                comuna_doc = meta.get("comuna", "general")
                if comuna_doc.lower() == "general":
                    comuna_doc = "General (Aplica a todas las comunas del país)"
                contexto += f"\n[Documento Oficial: {file_name}] | [Ámbito: {comuna_doc}]\n{res[0]}\n"
        else:
            contexto = f"SIN INFORMACIÓN disponible para la comuna '{comuna_busqueda}' en la base de datos."
 
    except Exception as e:
        logger.error(f"❌ Error RAG Supabase: {e}")
        contexto = "Error temporal al acceder a las normativas municipales."
 
    return contexto


COMUNAS_SOPORTADAS = ["recoleta", "el bosque"]
 
# Lista amplia SOLO para detectar cuando el usuario pregunta por una comuna
# que no está soportada (para no confundir "no la mencionó" con "preguntó por
# otra comuna que no cubrimos"). Amplía esta lista si quieres más cobertura.
OTRAS_COMUNAS_CONOCIDAS = [
    "providencia", "santiago", "ñuñoa", "nunoa", "maipú", "maipu",
    "la florida", "puente alto", "las condes", "vitacura", "peñalolén",
    "penalolen", "quilicura", "independencia", "conchalí", "conchali",
    "huechuraba", "renca", "cerro navia", "quinta normal", "estación central",
    "estacion central", "pedro aguirre cerda", "san miguel", "la cisterna",
    "lo espejo", "san ramón", "san ramon", "la granja", "macul", "peñaflor",
    "penaflor", "colina", "lampa", "til til", "melipilla",
]


def detectar_comuna(message: str, comuna_perfil: str) -> dict:
    """
    Determina sobre qué comuna debe responder el asistente.
 
    Devuelve:
        {
            "comuna": str,        # comuna a usar en el retrieval
            "soportada": bool,    # si tenemos datos de esa comuna
            "explicita": bool,    # si el usuario la mencionó explícitamente
        }
    """
    mensaje_lower = message.lower()
 
    # 1. ¿Menciona explícitamente una de las comunas soportadas?
    for comuna in COMUNAS_SOPORTADAS:
        if comuna in mensaje_lower:
            return {"comuna": comuna, "soportada": True, "explicita": True}
 
    # 2. ¿Menciona explícitamente OTRA comuna que no cubrimos?
    for comuna in OTRAS_COMUNAS_CONOCIDAS:
        if comuna in mensaje_lower:
            return {"comuna": comuna, "soportada": False, "explicita": True}
 
    # 3. No mencionó ninguna comuna -> usar la del perfil
    comuna_perfil_normalizada = (comuna_perfil or "").lower().strip()
    return {
        "comuna": comuna_perfil_normalizada,
        "soportada": comuna_perfil_normalizada in COMUNAS_SOPORTADAS,
        "explicita": False,
    }


async def get_ai_response(user: dict, message: str, ollama_available: bool, background_tasks=None) -> str:
    """
    RAG Avanzado compatible con Ollama y Groq Cloud.
    Consume el modelo de embeddings precargado en memoria global para evitar lags.
    Incluye memoria conversacional de largo plazo (resumen progresivo) y router
    de comuna (Recoleta / El Bosque).
    """
    phone = user.get("phone")
    comuna_usuario = (user.get("comuna") or "").lower().strip()
 
    # ── 1. RECUPERACIÓN RAG (con router de comuna integrado) ──
    contexto_rag = await obtener_contexto_rag(message, comuna_usuario)
 
    # ── 2. PROMPT AUMENTADO (perfil + progreso) ──
    roadmap = user.get("roadmap") or []
    completed = sum(1 for h in roadmap if h.get("done"))
    total = len(roadmap)
    current_hito = next((h for h in roadmap if not h.get("done")), None)
    progreso = f"{completed}/{total} hitos completados"
    if current_hito:
        progreso += f". Siguiente hito: {current_hito['title']}"
 
    system = SYSTEM_PROMPT.format(
        rubro=user.get("rubro", "No definido"),
        comuna=user.get("comuna", "No definida"),
        estado_sii="Formalizado" if user.get("inicio_sii") == "si" else "No formalizado",
        progreso=progreso,
    )
 
    # ── 3. MEMORIA DE LARGO PLAZO (resumen conversacional) ──
    resumen_previo = user.get("resumen_conversacion") or "Sin historial previo relevante."
 
    system_con_rag = (
        f"{system}\n\n"
        f"[RESUMEN DE INTERACCIONES PREVIAS]:\n"
        f"{resumen_previo}\n\n"
        f"[INFORMACIÓN MUNICIPAL OFICIAL DISPONIBLE]:\n"
        f"Usa prioritariamente este contexto para responder. Si el ámbito dice 'General', considera que aplica perfectamente para el usuario.\n"
        f"{contexto_rag}"
    )
 
    # ── 4. HISTORIAL RECIENTE DE CONVERSACIÓN ──
    history = get_messages(phone, limit=6) if phone else user.get("conversation_history", [])[-6:]
    messages = [{"role": "system", "content": system_con_rag}]
    messages.extend(history)
    messages.append({"role": "user", "content": message})
 
    # ── 5. GENERACIÓN DE RESPUESTA ──
    ai_text = llamar_llm(messages, max_tokens=600, temperature=0.2)
 
    if not ai_text:
        return "😅 Tuve un problema al procesar tu consulta con el modelo. ¿Puedes intentar de nuevo?"
 
    # ── 6. PERSISTENCIA EN BASE DE DATOS ──
    if phone:
        save_message(phone, "user", message)
        save_message(phone, "assistant", ai_text)
 
        total_mensajes = contar_mensajes(phone)
        if total_mensajes and total_mensajes % 10 == 0:
            actualizar_resumen_en_background(phone, background_tasks)
    else:
        history = user.get("conversation_history", [])
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": ai_text})
        user["conversation_history"] = history[-12:]
 
    return ai_text


async def process_ai_and_send(phone_whatsapp: str, phone_clean: str, message: str, get_user_fn, save_user_fn, twilio_client, ollama_available: bool):
    """Process AI query and send response via Twilio (runs in background)."""
    user = get_user_fn(phone_clean)
    if not user:
        return

    ai_response = await get_ai_response(user, message, ollama_available)
    save_user_fn(phone_clean, user)

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