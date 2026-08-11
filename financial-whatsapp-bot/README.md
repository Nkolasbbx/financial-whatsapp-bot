# FinancIAl WhatsApp Bot

Bot de WhatsApp para orientar a microemprendedores chilenos. Usa FastAPI, Meta WhatsApp Cloud API, Supabase y un modelo compatible con la API de OpenAI (Groq u Ollama).

## Arquitectura

```text
Emprendedor (WhatsApp)
        |
        v
Meta WhatsApp Cloud API
        |
        v
FastAPI ---- Groq/Ollama
        |
        v
Supabase
```

## Preparación

Desde esta carpeta:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Completa `.env` con tus credenciales reales. El archivo ya está ignorado por Git.

## Variables de Meta

```env
WHATSAPP_PROVIDER=meta
META_WHATSAPP_TOKEN=
META_PHONE_NUMBER_ID=
META_WABA_ID=
META_WEBHOOK_VERIFY_TOKEN=
META_APP_SECRET=
META_GRAPH_API_VERSION=
```

- `META_WHATSAPP_TOKEN`: token temporal de pruebas o token permanente de un System User.
- `META_PHONE_NUMBER_ID`: identificador del número, no el número visible.
- `META_WABA_ID`: identificador de la cuenta de WhatsApp Business.
- `META_WEBHOOK_VERIFY_TOKEN`: secreto definido por el equipo; debe coincidir con el ingresado al registrar el webhook.
- `META_APP_SECRET`: clave secreta de la aplicación, usada para validar `X-Hub-Signature-256`.
- `META_GRAPH_API_VERSION`: versión mostrada por Meta, incluyendo la `v` inicial.

## Ejecutar localmente

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000
```

Verifica el estado en:

```text
http://127.0.0.1:8000/
```

## Exponer el webhook

En otra terminal:

```powershell
ngrok http 8000
```

Usa la URL HTTPS entregada por ngrok:

```text
https://tu-subdominio.ngrok-free.app/webhook/whatsapp
```

## Configurar el webhook en Meta

En la aplicación de Meta, entra a **WhatsApp → Configuración → Webhook**.

Configura:

```text
Callback URL: https://tu-subdominio.ngrok-free.app/webhook/whatsapp
Verify token: el mismo valor de META_WEBHOOK_VERIFY_TOKEN
```

Luego suscribe el campo `messages`.

El backend expone:

- `GET /webhook/whatsapp`: responde al desafío de verificación de Meta.
- `POST /webhook/whatsapp`: recibe mensajes y estados, y valida la firma de Meta.

## Configuración de IA

Para Groq:

```env
OLLAMA_URL=https://api.groq.com/openai/v1
OLLAMA_MODEL=llama-3.3-70b-versatile
IA_API_KEY=
```

Para Ollama o un túnel compatible, cambia URL y modelo y deja `IA_API_KEY` vacía.

## Supabase

```env
SUPABASE_URL=
SUPABASE_KEY=
DB_DSN=
```

`SUPABASE_URL` y `SUPABASE_KEY` almacenan perfiles y mensajes. `DB_DSN` permite la búsqueda RAG directa en Postgres.

## Comandos del bot

| Comando | Acción |
|---|---|
| `hola` | Inicia el onboarding |
| `mi roadmap` | Muestra el progreso |
| `listo` | Completa el hito actual |
| `postular a fondo` | Simula una postulación |
| `ayuda` | Muestra el menú |
| `reiniciar` | Reinicia el perfil |
| Cualquier pregunta | Respuesta contextual con IA |

## Producción

Antes de producción:

1. Sustituye el token temporal por un token permanente de Meta.
2. Usa una URL HTTPS estable en lugar de ngrok.
3. Registra el número definitivo de WhatsApp Business.
4. Crea plantillas aprobadas para mensajes iniciados fuera de la ventana de atención.
5. Configura los secretos únicamente como variables del entorno del hosting.

El `Procfile` permite iniciar la aplicación en plataformas compatibles:

```text
web: uvicorn main:app --host 0.0.0.0 --port $PORT
```
