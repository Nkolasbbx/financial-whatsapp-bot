# Embedding Service — Guía de ejecución local con ngrok

Microservicio de embeddings para el RAG de FinancIAl. Corre el modelo
`sentence-transformers` en memoria y expone un endpoint HTTP para que el
bot le pida vectores en vez de cargar el modelo localmente.

Esta guía es para correrlo **en tu máquina** mientras se decide el
despliegue definitivo (Railway/Fly.io), exponiéndolo a internet con
**ngrok** para que tu bot (local o en Vercel) pueda alcanzarlo.

---

## 1. Requisitos previos

- Python 3.10+
- [ngrok](https://ngrok.com/download) instalado y con cuenta (el plan gratuito alcanza)
- ~2-3 GB de espacio libre (el modelo de embeddings se descarga la primera vez)

---

## 2. Instalar dependencias

Desde la carpeta `embedding-service/`:

```bash
python3 -m venv venv
source venv/bin/activate        # En Windows: venv\Scripts\activate

pip install -r requirements.txt
```

---

## 3. Configurar variables de entorno

```bash
cp .env.example .env
```

Revisa que `EMBEDDING_MODEL_NAME` en `.env` sea **exactamente el mismo
modelo** que se usó para generar los embeddings ya guardados en tu tabla
`documents` de Supabase. Si no coinciden, las búsquedas por similitud no
van a funcionar correctamente (los vectores no son comparables entre
modelos distintos).

```dotenv
EMBEDDING_MODEL_NAME=intfloat/multilingual-e5-base
```

---

## 4. Levantar el servicio

```bash
uvicorn main:app --reload --port 8001
```

La primera vez que corras esto, `sentence-transformers` va a **descargar
el modelo** (puede tardar unos minutos según tu conexión). Vas a ver algo
así en la consola cuando esté listo:

```
INFO:embedding-service:🔄 Cargando modelo de embeddings: intfloat/multilingual-e5-base...
INFO:embedding-service:✅ Modelo cargado y listo.
INFO:     Uvicorn running on http://127.0.0.1:8001
```

### Probarlo localmente antes de exponerlo

```bash
curl -X POST http://localhost:8001/embed \
  -H "Content-Type: application/json" \
  -d '{"text": "requisitos patente comercial", "prefix": "query"}'
```

Deberías recibir un JSON con un vector de números (`embedding`) y su
dimensión (`dimensions`). Si esto funciona, el servicio está listo para
exponerse.

También puedes chequear que sigue vivo con:
```bash
curl http://localhost:8001/health
```

---

## 5. Exponerlo a internet con ngrok

En **otra terminal** (deja el `uvicorn` corriendo en la primera):

```bash
ngrok http 8001
```

Vas a ver algo así:

```
Forwarding    https://a1b2-190-123-45-67.ngrok-free.app -> http://localhost:8001
```

Esa URL (`https://a1b2-....ngrok-free.app`) es la que le pasas a tu bot.

⚠️ **Importante sobre el plan gratuito de ngrok:**
- La URL **cambia cada vez que reinicias ngrok** (a menos que tengas un dominio fijo reservado en tu cuenta, disponible en planes pagos). Si reinicias, tienes que actualizar la variable de entorno del bot con la nueva URL.
- ngrok gratuito muestra una página de advertencia intermedia en el navegador (no afecta llamadas API hechas por código/`httpx`, solo si abres la URL en un navegador manualmente).

---

## 6. Conectar el bot a este servicio

En el `.env` de tu **bot principal** (`financial-whatsapp-bot/`), agrega o actualiza:

```dotenv
EMBEDDING_SERVICE_URL=https://a1b2-190-123-45-67.ngrok-free.app
```

(reemplaza por la URL real que te dio ngrok en el paso anterior)

Reinicia tu bot para que tome la nueva variable de entorno, y prueba una
consulta que dispare el RAG (ej. preguntar por la patente comercial) para
confirmar que el flujo completo funciona: bot → ngrok → tu máquina local
→ modelo de embeddings → resultado de vuelta.

---

## 7. Probar el flujo end-to-end

```bash
# Simula lo que hace el bot: pedirle un embedding a través de ngrok
curl -X POST https://a1b2-190-123-45-67.ngrok-free.app/embed \
  -H "Content-Type: application/json" \
  -d '{"text": "necesito la patente en recoleta", "prefix": "query"}'
```

Si responde igual que cuando lo probaste en `localhost`, el túnel está
funcionando correctamente y tu bot ya puede usarlo.

---

## Notas y limitaciones de este setup temporal

- **Esto es solo para desarrollo/pruebas.** Si cierras la terminal de
  `uvicorn` o de `ngrok`, o apagas tu computador, el servicio deja de
  responder y el RAG de tu bot fallará (revisa que `obtener_contexto_rag`
  tenga su manejo de errores, para que al menos no rompa toda la
  respuesta del bot si esto pasa).
- **No uses esta URL de ngrok en producción real** — es para validar que
  la arquitectura (bot → microservicio → modelo) funciona antes de
  desplegar el microservicio en un servicio persistente real
  (Railway/Fly.io), que es el paso siguiente.
- Si tu bot corre en Vercel (no local), igual puede alcanzar esta URL de
  ngrok sin problema — ngrok expone el túnel a internet público, no solo
  a tu red local.

---

## Siguiente paso

Cuando quieras dejar de depender de tu máquina encendida + ngrok, el
mismo código de este `main.py` se despliega tal cual en Railway usando
el `Procfile` incluido — solo cambias `EMBEDDING_SERVICE_URL` en el bot
por la URL fija que te da Railway.
