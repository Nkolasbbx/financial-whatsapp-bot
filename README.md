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
SUPABASE_SERVICE_ROLE_KEY=
DB_DSN=
```

`SUPABASE_URL` y `SUPABASE_KEY` almacenan perfiles y mensajes.
`SUPABASE_SERVICE_ROLE_KEY` se utiliza únicamente en el backend para procesar
los recordatorios protegidos por RLS. `DB_DSN` permite la búsqueda RAG directa
en Postgres.

## Comandos del bot

| Comando | Acción |
|---|---|
| `hola` | Inicia el onboarding |
| `mi roadmap` | Muestra el progreso |
| `listo` | Completa el hito actual |
| `postular a fondo` | Simula una postulación |
| `ayuda` | Muestra el menú |
| `reiniciar` | Reinicia el perfil |
| `activar recordatorios` | Acepta recordatorios después de 3 días sin avance |
| `pausar recordatorios` | Detiene futuros recordatorios |
| Cualquier pregunta | Respuesta contextual con IA |

## Recordatorios proactivos

Los avisos 1 y 2 utilizan `recordatorio_roadmap`; el tercer y último aviso usa
`last_reminder_roadmap`. Ambas plantillas reciben `{{1}}` como etiqueta del
destinatario y `{{2}}` como título del hito pendiente.

```env
REMINDERS_ENABLED=false
REMINDER_TEMPLATE_NAME=recordatorio_roadmap
REMINDER_FINAL_TEMPLATE_NAME=last_reminder_roadmap
REMINDER_TEMPLATE_LANGUAGE=es_CL
REMINDER_RECIPIENT_LABEL=emprendedor/a
REMINDER_DAYS=3
REMINDER_TIMEZONE=America/Santiago
REMINDER_BATCH_SIZE=50
CRON_SECRET=
```

El mismo endpoint acepta `GET` y `POST`. Vercel Cron utiliza `GET`; para una
prueba manual también puede utilizarse `POST`. Ambos requieren:

```text
Authorization: Bearer CRON_SECRET
```

El endpoint se encuentra fuera del esquema público de OpenAPI. Los intentos,
estados de entrega y respuestas se guardan en `reminder_deliveries`.

### Probar la rutina localmente

Con Uvicorn levantado y el mismo `CRON_SECRET` configurado en `.env`:

```powershell
$cronSecret = "valor_configurado_en_tu_env_local"
Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8000/internal/reminders/run" `
    -Headers @{ Authorization = "Bearer $cronSecret" }
```

La petición también puede realizarse con `GET`, que reproduce la llamada de
Vercel:

```powershell
Invoke-RestMethod `
    -Method Get `
    -Uri "http://127.0.0.1:8000/internal/reminders/run" `
    -Headers @{ Authorization = "Bearer $cronSecret" }
```

## Despliegue en Vercel

El archivo `vercel.json` registra una ejecución diaria de:

```text
GET /internal/reminders/run
Horario: 0 13 * * * (13:00 UTC)
```

En Chile corresponde aproximadamente a las 09:00 en horario de invierno y a
las 10:00 en horario de verano. Vercel Cron trabaja siempre en UTC.

### 1. Preparar el código

1. Confirma que los cambios estén en la rama que despliega el proyecto.
2. Sube la rama a GitHub.
3. Integra los cambios en la rama de producción conectada a Vercel.
4. Comprueba que el nuevo deployment finalice correctamente.

Los cron jobs se activan solamente en deployments de producción, no en los
deployments Preview.

### 2. Configurar las variables de producción

En **Vercel → Project → Settings → Environment Variables**, agrega las
variables descritas en `.env.example`, al menos:

```env
DEBUG=false
META_WHATSAPP_TOKEN=
META_PHONE_NUMBER_ID=
META_WABA_ID=
META_WEBHOOK_VERIFY_TOKEN=
META_APP_SECRET=
META_GRAPH_API_VERSION=v26.0
SUPABASE_URL=
SUPABASE_KEY=
SUPABASE_SERVICE_ROLE_KEY=
REMINDERS_ENABLED=true
REMINDER_TEMPLATE_NAME=recordatorio_roadmap
REMINDER_FINAL_TEMPLATE_NAME=last_reminder_roadmap
REMINDER_TEMPLATE_LANGUAGE=es_CL
REMINDER_RECIPIENT_LABEL=emprendedor/a
REMINDER_DAYS=3
REMINDER_TIMEZONE=America/Santiago
REMINDER_BATCH_SIZE=50
CRON_SECRET=
```

Genera un secreto nuevo y largo para `CRON_SECRET`. Vercel enviará ese valor
automáticamente en el encabezado `Authorization: Bearer ...`; no se configura
el encabezado manualmente en el panel. Después de agregar o cambiar variables,
vuelve a desplegar el proyecto.

### 3. Activar y revisar el cron

Después del deployment de producción:

1. Entra a **Vercel → Project → Settings → Cron Jobs**.
2. Confirma que aparezca `/internal/reminders/run`.
3. Revisa sus ejecuciones en **Logs**.
4. Comprueba en Supabase los cambios de `users` y los registros de
   `reminder_deliveries`.

En el plan Hobby la rutina puede ejecutarse una vez al día y Vercel puede
iniciarla en cualquier momento dentro de la hora configurada. Además, Vercel
no reintenta automáticamente una ejecución fallida.

### 4. Restaurar el webhook de Meta

Una vez validado el deployment, configura en Meta la URL estable:

```text
https://TU-DOMINIO-VERCEL/webhook/whatsapp
```

El identificador de verificación debe coincidir con
`META_WEBHOOK_VERIFY_TOKEN`, y el campo `messages` debe continuar suscrito.
Cambiar el webhook de ngrok a Vercel no modifica los cron jobs.

### 5. Comprobación inicial

Para una prueba controlada, usa un usuario propio con recordatorios activados y
una fecha `next_reminder_at` vencida. Primero invoca manualmente el endpoint de
producción con `Authorization: Bearer ...`. Cuando el resultado sea correcto,
deja la ejecución diaria a cargo de Vercel.

## Producción

Antes de producción:

1. Sustituye el token temporal por un token permanente de Meta.
2. Usa una URL HTTPS estable en lugar de ngrok.
3. Registra el número definitivo de WhatsApp Business.
4. Crea plantillas aprobadas para mensajes iniciados fuera de la ventana de atención.
5. Configura los secretos únicamente como variables del entorno del hosting.
6. Verifica el cron diario y sus logs antes de habilitar un lote grande.

El `Procfile` permite iniciar la aplicación en plataformas compatibles:

```text
web: uvicorn main:app --host 0.0.0.0 --port $PORT
```
