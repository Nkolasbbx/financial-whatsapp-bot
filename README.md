# 🚀 FinancIAl WhatsApp Bot - Guía de Setup

## Arquitectura

```
Emprendedor (WhatsApp)
        │
        ▼
   Twilio (webhook)
        │
        ▼
   FastAPI Backend ─── OpenAI (GPT-4o-mini)
        │
        ▼
   Supabase (perfiles + roadmaps)
```

## Setup en 30 minutos

### Paso 1: Clonar y preparar el proyecto

```bash
# Clonar o copiar los archivos
cd financial-whatsapp-bot

# Crear entorno virtual
python -m venv venv
source venv/bin/activate        # Mac/Linux
# venv\Scripts\activate         # Windows

# Instalar dependencias
pip install -r requirements.txt

# Copiar variables de entorno
cp .env.example .env
```

### Paso 2: Configurar OpenAI (5 min)

1. Ir a https://platform.openai.com/api-keys
2. Crear una API key
3. Copiar la key en `.env` → `OPENAI_API_KEY=sk-...`

> 💡 GPT-4o-mini cuesta ~$0.15 por 1M tokens. Para el MVP gastarán menos de $1.

### Paso 3: Configurar Twilio Sandbox (10 min)

1. Crear cuenta en https://www.twilio.com/ (gratis)
2. Ir a **Console → Messaging → Try it out → Send a WhatsApp message**
3. Seguir las instrucciones para activar el sandbox:
   - Te darán un número (ej: +14155238886)
   - Te darán un código (ej: "join bright-cloud")
4. Copiar credenciales en `.env`:
   - `TWILIO_ACCOUNT_SID` → está en el Dashboard principal
   - `TWILIO_AUTH_TOKEN` → está en el Dashboard principal
   - `TWILIO_WHATSAPP_NUMBER` → el número del sandbox

### Paso 4: Exponer el servidor (ngrok)

Twilio necesita una URL pública para enviar los webhooks.

```bash
# Instalar ngrok: https://ngrok.com/download
# O con brew: brew install ngrok

# En una terminal, levantar el servidor:
uvicorn main:app --reload --port 8000

# En OTRA terminal, exponer con ngrok:
ngrok http 8000
```

ngrok te dará una URL como `https://abc123.ngrok-free.app`

### Paso 5: Conectar Twilio con tu servidor

1. Ir a **Twilio Console → Messaging → Settings → WhatsApp Sandbox Settings**
2. En "When a message comes in", poner:
   ```
   https://abc123.ngrok-free.app/webhook/whatsapp
   ```
   (usar TU url de ngrok)
3. Método: **POST**
4. Guardar

### Paso 6: ¡Probar!

1. Desde tu celular, envía el código de unión al sandbox
   (ej: enviar "join bright-cloud" al +14155238886 por WhatsApp)
2. Luego escribe "hola" y el bot debería responder

Para que otros prueben (emprendedores, profesor), cada persona debe:
1. Agregar el número del sandbox a sus contactos
2. Enviar el código de unión
3. ¡Listo! Ya pueden chatear con FinancIAl

---

## Modo de prueba sin Twilio

Si quieres probar la lógica sin WhatsApp:

```bash
# Solo necesitas OpenAI configurado (o ni eso para el flujo básico)
uvicorn main:app --reload --port 8000
```

Abre http://localhost:8000/test/chat en el navegador.
Ahí tienes un chat de prueba que simula la conversación.

---

## Configurar Supabase (opcional)

Sin Supabase, los datos se guardan en memoria (se pierden al reiniciar).
Para persistencia:

1. Crear proyecto en https://supabase.com/
2. Ir a **SQL Editor** y ejecutar el contenido de `schema.sql`
3. Ir a **Settings → API** y copiar:
   - `SUPABASE_URL` → Project URL
   - `SUPABASE_KEY` → anon/service_role key
4. Pegar en `.env`

---

## Comandos disponibles en el bot

| Comando | Qué hace |
|---------|----------|
| `hola` | Inicia el onboarding |
| `mi roadmap` | Muestra el progreso de formalización |
| `listo` | Marca el hito actual como completado |
| `postular a fondo` | Simula postulación a fondos |
| `ayuda` | Muestra el menú |
| `reiniciar` | Borra el perfil y empieza de nuevo |
| _(cualquier otra cosa)_ | Responde con IA contextualizada |

---

## Deploy en producción (Vercel / Railway)

### Railway (recomendado para FastAPI)

1. Ir a https://railway.app/
2. New Project → Deploy from GitHub
3. Agregar variables de entorno
4. Railway da una URL pública automáticamente
5. Configurar esa URL en Twilio

### Procfile para Railway:
```
web: uvicorn main:app --host 0.0.0.0 --port $PORT
```

---

## Estructura del proyecto

```
financial-whatsapp-bot/
├── main.py              # Toda la lógica del bot
├── requirements.txt     # Dependencias Python
├── schema.sql           # Schema de Supabase
├── .env.example         # Template de variables
├── .env                 # Variables reales (NO subir a Git)
├── Procfile             # Para deploy en Railway
└── README.md            # Esta guía
```

---

## FAQ

**¿Cuánto cuesta?**
- Twilio sandbox: gratis
- OpenAI GPT-4o-mini: ~$0.15/1M tokens (menos de $1 para el MVP)
- Supabase: gratis (tier free hasta 500MB)
- ngrok: gratis (tier free)
- Total MVP: ~$0-1 USD

**¿Cuántos usuarios soporta el sandbox?**
- Sin límite de usuarios, pero cada uno debe enviar el código de unión
- Para producción real, necesitan verificar su número de WhatsApp Business ($)

**¿Los datos se pierden?**
- Sin Supabase: sí, al reiniciar el servidor
- Con Supabase: no, todo persiste

**¿Puedo probar sin WhatsApp?**
- Sí, abre http://localhost:8000/test/chat en el navegador
