import logging
import httpx
import os
from config import OLLAMA_URL, OLLAMA_MODEL, TWILIO_WHATSAPP_NUMBER,DB_DSN
from db.users import save_message, get_messages
from sentence_transformers import SentenceTransformer
import psycopg2
import dependencies
logger = logging.getLogger("financial")

SYSTEM_PROMPT = """Eres FinancIAl, un asistente virtual de WhatsApp experto en guiar a microemprendedores chilenos en su proceso de formalización y crecimiento.
 
REGLAS DE COMPORTAMIENTO Y TONO:
- Responde SIEMPRE en español chileno, con un tono cercano, empático y muy simple.
- Tus respuestas deben ser BREVES y al grano: máximo 3-4 oraciones. Esto es WhatsApp, evita bloques densos de texto.
- NUNCA uses tecnicismos legales o tributarios a secas; explícalos siempre con un ejemplo cotidiano del rubro del usuario.
- Usa emojis con moderación para mantener la conversación amigable pero profesional.
- Formatea usando *negritas* para conceptos clave y _cursivas_ para ejemplos, respetando el formato de WhatsApp.
 
📋 REGLA ESTRICTA DE CONTROL RAG (PROHIBIDO INVENTAR):
- Para responder preguntas sobre trámites, patentes o requisitos municipales, debes basarte ÚNICAMENTE en la información del bloque [INFORMACIÓN MUNICIPAL OFICIAL] de abajo.
- Ese bloque solo contiene información de la comuna *{comuna}*. Si la pregunta es sobre otra comuna, responde que solo manejas info de {comuna}.
- Si el bloque viene vacío o marcado como SIN INFORMACIÓN, responde textualmente: "Pucha, no manejo esa información específica para *{comuna}* en este momento. Te sugiero consultar directamente en el departamento de patentes de tu Municipalidad para ir a la segura. 🏢"
- Está TERMINANTEMENTE PROHIBIDO inventar plazos, departamentos, costos o requisitos que no estén explícitamente escritos en el contexto provisto. Si no está escrito, no lo digas.
- NUNCA extrapoles información de una comuna a otra. Lo que aplica en Recoleta NO necesariamente aplica en Maipú ni en ninguna otra comuna.
 
CONTEXTO ACTUAL DEL EMPRENDEDOR:
- Rubro: {rubro}
- Comuna: {comuna}
- Estado SII: {estado_sii}
- Progreso en FinancIAl: {progreso}
 
Considera siempre este perfil para personalizar tu respuesta sin pedirle al usuario que se repita."""

def get_ai_response(user: dict, message: str, ollama_available: bool) -> str:
    """
    RAG Avanzado compatible con Ollama y Groq Cloud.
    Consume el modelo de embeddings precargado en memoria global para evitar lags.
    """
    phone = user.get("phone")
    comuna_usuario = (user.get("comuna") or "").lower().strip()
    
    # Extraemos las credenciales del entorno (.env)
    ollama_url = os.getenv("OLLAMA_URL")
    ollama_model = os.getenv("OLLAMA_MODEL")
    ia_api_key = os.getenv("IA_API_KEY", "")
 
    # ── 1. RECUPERACIÓN RAG FILTRADA POR COMUNA + GENERAL (OPTIMIZADA) ──
    contexto_rag = ""
    try:
        # 🔥 SOLUCIÓN: El bloque if embedding_model is None se eliminó.
        # Generamos el vector directo usando la RAM global con el prefijo estricto de e5.
        query_vector = query_vector = dependencies.embedding_model.encode(f"query: {message}").tolist()
 
        conn = psycopg2.connect(DB_DSN)
        with conn.cursor() as cur:
            if comuna_usuario:
                # Trae lo específico de la comuna del usuario O lo común/transversal ('general')
                cur.execute("""
                    SELECT content, metadata
                    FROM documents
                    WHERE metadata->>'comuna' ILIKE %s OR metadata->>'comuna' ILIKE '%%general%%'
                    ORDER BY embedding <=> %s::vector
                    LIMIT 4;
                """, (f"%{comuna_usuario}%", query_vector))
            else:
                # Sin comuna definida en el perfil, busca libremente en toda la base de datos
                cur.execute("""
                    SELECT content, metadata
                    FROM documents
                    ORDER BY embedding <=> %s::vector
                    LIMIT 4;
                """, (query_vector,))
 
            resultados = cur.fetchall()
        conn.close()
 
        if resultados:
            for res in resultados:
                meta = res[1] if res[1] else {}
                file_name = meta.get("file_name", "Municipal")
                comuna_doc = meta.get("comuna", "general")
                
                # Explicitar la validez a la IA para evitar bloqueos lógicos en el prompt
                if comuna_doc.lower() == "general":
                    comuna_doc = "General (Aplica a todas las comunas del país)"
                
                contexto_rag += f"\n[Documento Oficial: {file_name}] | [Ámbito: {comuna_doc}]\n{res[0]}\n"
        else:
            # Sin resultados para esta comuna — avisa al modelo explícitamente
            contexto_rag = f"SIN INFORMACIÓN disponible para la comuna '{comuna_usuario}' en la base de datos."
 
    except Exception as e:
        logger.error(f"❌ Error RAG Supabase: {e}")
        contexto_rag = "Error temporal al acceder a las normativas municipales."
 
    # ── 2. PROMPT AUMENTADO ──
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
 
    system_con_rag = (
        f"{system}\n\n"
        f"[INFORMACIÓN MUNICIPAL OFICIAL DISPONIBLE]:\n"
        f"Usa prioritariamente este contexto para responder. Si el ámbito dice 'General', considera que aplica perfectamente para el usuario.\n"
        f"{contexto_rag}"
    )
 
    # ── 3. HISTORIAL DE CONVERSACIÓN ──
    history = get_messages(phone, limit=6) if phone else user.get("conversation_history", [])[-6:]
    messages = [{"role": "system", "content": system_con_rag}]
    messages.extend(history)
    messages.append({"role": "user", "content": message})
 
    # ── 4. GENERACIÓN DE RESPUESTA (DINÁMICA: LOCAL / NGROK / CLOUD) ──
    try:
        # Configuramos dinámicamente las cabeceras según el proveedor
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
 
        # Construimos el endpoint de chat estándar de OpenAI
        logger.info(f"🚀 Despachando inferencia a: {endpoint_url}")
 
        response = httpx.post(
            endpoint_url,
            headers=headers,
            json={
                "model": ollama_model,
                "messages": messages,
                "stream": False,
                "temperature": 0.2,   # Temperatura baja para garantizar precisión técnica
                "max_tokens": 600,    # Más espacio para que enumere listas sin cortarse
            },
            timeout=60,
        )
 
        response.raise_for_status()
        data = response.json()
        ai_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
 
        if not ai_text:
            return "😅 No pude procesar los datos de la respuesta. ¿Puedes intentar de nuevo?"
 
        # ── 5. PERSISTENCIA EN BASE DE DATOS ──
        if phone:
            save_message(phone, "user", message)
            save_message(phone, "assistant", ai_text)
        else:
            history = user.get("conversation_history", [])
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": ai_text})
            user["conversation_history"] = history[-12:]
 
        return ai_text
 
    except httpx.TimeoutException:
        logger.error("⏳ Timeout al conectar con el servidor de IA")
        return "⏳ El servidor del modelo se demoró mucho en responder. Intenta con una pregunta más corta."
    except Exception as e:
        logger.error(f"💥 Error crítico en Generación de IA: {e}")
        return "😅 Tuve un problema al procesar tu consulta con el modelo. ¿Puedes intentar de nuevo?"


def process_ai_and_send(phone_whatsapp: str, phone_clean: str, message: str, get_user_fn, save_user_fn, twilio_client, ollama_available: bool):
    """Process AI query and send response via Twilio (runs in background)."""
    user = get_user_fn(phone_clean)
    if not user:
        return

    ai_response = get_ai_response(user, message, ollama_available)
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



