import dependencies
from fastapi import APIRouter, Request, Response

from services.message_router import route_message
from core.ia import get_ai_response
from db.users import get_user, save_user

router = APIRouter()

@router.get("/test/rag-database")
def test_rag_database(q: str):
    """
    Endpoint rápido y autónomo para verificar la similitud de coseno en Supabase.
    Invócalo desde el navegador: http://127.0.0.1:8000/test/rag-database?q=extintor
    """
    import psycopg2
    import os
    from sentence_transformers import SentenceTransformer
    
    try:
        # Inicializamos el modelo de forma local y segura para el test
        print("📦 Cargando modelo de embeddings para la prueba...")
        model = SentenceTransformer("intfloat/multilingual-e5-base")
            
        # Generar el vector con la regla estricta del modelo
        # (Asegúrate de incluir el prefijo 'query: ')
        query_vector = model.encode(f"query: {q}").tolist()
        
        # Conectar a la DB usando tu .env con el proxy IPv4
        db_url = os.getenv("DB_DSN")
        print(f"🔌 Conectando a Supabase usando: {db_url[:35]}...")
        
        conn = psycopg2.connect(db_url)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT content, metadata 
                FROM documents 
                ORDER BY embedding <=> %s::vector 
                LIMIT 3;
            """, (query_vector,))
            rows = cur.fetchall()
        conn.close()
        
        # Estructurar la respuesta para el navegador
        resultados_limpios = []
        for r in rows:
            resultados_limpios.append({
                "texto": r[0],
                "metadata": r[1]
            })
            
        return {
            "status": "success",
            "query_recibida": q,
            "total_encontrado": len(rows),
            "resultados": resultados_limpios
        }
    except Exception as e:
        import logging
        logging.error(f"❌ Error en el endpoint de prueba: {e}")
        return {"status": "error", "detalle": str(e)}

@router.post("/test/chat")
async def test_chat(request: Request):
    """Test endpoint - simulates WhatsApp without Twilio."""
    data = await request.json()
    phone = data.get("phone", "+56900000000")
    message = data.get("message", "")

    response = route_message(phone, message)

    if response == "__AI_QUERY__":
        user = get_user(phone)
        response = get_ai_response(user, message, dependencies.ollama_available)
        save_user(phone, user)

    return {"response": response, "phone": phone}


@router.get("/test/chat")
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