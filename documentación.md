# Documentación del proyecto FinancIAl

## 1. Resumen

FinancIAl es un bot de WhatsApp orientado a microemprendedores chilenos. Su objetivo es acompañar el proceso de formalización de un negocio mediante:

- Onboarding para conocer rubro, comuna y situación ante el SII.
- Roadmaps personalizados por tipo de emprendimiento.
- Consulta y avance de hitos mediante comandos de WhatsApp.
- Orientación contextual generada por IA.
- Recuperación de información municipal mediante RAG.
- Persistencia de usuarios y conversaciones en Supabase.
- Integración directa con WhatsApp Business Platform mediante Meta Cloud API.

El proyecto ya no utiliza Twilio. La comunicación con WhatsApp se realiza directamente contra Graph API de Meta.

## 2. Estado actual

| Componente | Estado |
|---|---|
| Webhook de Meta | Implementado y validado |
| Recepción de mensajes reales | Implementada |
| Envío de texto por Graph API | Implementado |
| Respuestas interactivas | Implementadas para botones y listas |
| Verificación de firma de Meta | Implementada |
| Onboarding | Implementado |
| Roadmaps | Implementados |
| IA con Groq u Ollama | Implementada |
| RAG con Supabase/pgvector | Implementado |
| Persistencia de usuarios y mensajes | Implementada |
| Envío de plantillas de Meta | Función base implementada |
| Recordatorios automáticos de inactividad | Pendiente |
| Token permanente de Meta | Debe configurarse para producción |
| Número definitivo de WhatsApp | Pendiente de registrar |

## 3. Arquitectura

```text
Usuario de WhatsApp
        |
        v
WhatsApp Business Platform / Meta Cloud API
        |
        | POST /webhook/whatsapp
        v
FastAPI
        |
        +--> Router de mensajes
        |      +--> Onboarding
        |      +--> Roadmap
        |      +--> Fondos
        |      +--> IA
        |
        +--> Supabase REST
        |      +--> users
        |      +--> messages
        |
        +--> PostgreSQL + pgvector
        |      +--> documents
        |
        +--> Groq Cloud u Ollama
        |
        +--> Meta Graph API
               +--> Respuestas de WhatsApp
```

## 4. Estructura del repositorio

```text
financial-whatsapp-bot/
├── documentación.md
├── RAG/
│   ├── data/                       # Contenido municipal procesado
│   ├── docs/                       # Documentos PDF originales
│   ├── env_example.env
│   └── ingest_supabase.py          # Ingesta y generación de embeddings
└── financial-whatsapp-bot/
    ├── core/
    │   ├── fondos.py               # Simulación de fondos
    │   ├── ia.py                   # LLM, RAG, memoria y respuesta de IA
    │   ├── onboarding.py           # Flujo inicial del usuario
    │   └── roadmaps.py             # Roadmaps y avance de hitos
    ├── db/
    │   └── users.py                # Persistencia de usuarios y mensajes
    ├── routers/
    │   ├── test.py                 # Endpoints locales de prueba
    │   └── webhook.py              # Webhook de Meta
    ├── services/
    │   ├── message_router.py       # Enrutamiento de comandos
    │   └── whatsapp.py             # Cliente de Meta Cloud API
    ├── .env                        # Secretos locales; no se versiona
    ├── .env.example                # Plantilla de configuración
    ├── config.py                   # Lectura de variables de entorno
    ├── dependencies.py             # Inicialización y cierre de clientes
    ├── main.py                     # Aplicación FastAPI
    ├── Procfile                    # Comando de despliegue
    ├── README.md                   # Guía rápida
    └── requirements.txt            # Dependencias Python
```

## 5. Requisitos

- Python 3.11 recomendado.
- Cuenta y aplicación en Meta for Developers.
- WhatsApp Business Account (WABA).
- Número de prueba de Meta o número definitivo registrado.
- Proyecto Supabase.
- Credenciales de Groq o servidor Ollama compatible.
- ngrok para desarrollo local, o una URL HTTPS pública estable.

## 6. Instalación local

Abrir PowerShell en la carpeta interna de la aplicación:

```powershell
cd "C:\Users\vladi\Desktop\Primer Semestre 2026\Feria\financial-whatsapp-bot\financial-whatsapp-bot"
```

Crear el entorno e instalar dependencias:

```powershell
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Crear el archivo local de configuración si todavía no existe:

```powershell
Copy-Item .env.example .env
```

Nunca subir `.env` al repositorio.

## 7. Variables de entorno

### 7.1 Meta WhatsApp

```env
WHATSAPP_PROVIDER=meta
META_WHATSAPP_TOKEN=
META_PHONE_NUMBER_ID=
META_WABA_ID=
META_WEBHOOK_VERIFY_TOKEN=
META_APP_SECRET=
META_GRAPH_API_VERSION=v26.0
```

| Variable | Descripción |
|---|---|
| `WHATSAPP_PROVIDER` | Proveedor activo. Actualmente debe ser `meta`. |
| `META_WHATSAPP_TOKEN` | Token temporal para pruebas o token permanente para producción. No debe incluir la palabra `Bearer`. |
| `META_PHONE_NUMBER_ID` | Identificador interno del número emisor. No es el teléfono visible. |
| `META_WABA_ID` | Identificador de la cuenta de WhatsApp Business. |
| `META_WEBHOOK_VERIFY_TOKEN` | Secreto definido por el equipo para validar el callback inicial. |
| `META_APP_SECRET` | Clave secreta de la aplicación de Meta. Permite validar la firma de cada webhook. |
| `META_GRAPH_API_VERSION` | Versión de Graph API. La `v` debe escribirse en minúscula. |

`META_APP_SECRET` se encuentra en:

```text
Meta for Developers
→ Aplicación
→ Configuración de la app
→ Básica
→ Clave secreta de la app
```

No debe confundirse con el identificador de acceso del cliente.

### 7.2 Supabase

```env
SUPABASE_URL=
SUPABASE_KEY=
DB_DSN=
```

| Variable | Descripción |
|---|---|
| `SUPABASE_URL` | URL pública del proyecto Supabase. |
| `SUPABASE_KEY` | Clave utilizada por el cliente REST. En backend debe protegerse como secreto. |
| `DB_DSN` | Cadena directa de PostgreSQL para consultas RAG con pgvector. |

### 7.3 IA

Para Groq:

```env
OLLAMA_URL=https://api.groq.com/openai/v1
OLLAMA_MODEL=llama-3.3-70b-versatile
IA_API_KEY=
```

Para Ollama local o expuesto mediante un túnel:

```env
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=nombre_del_modelo
IA_API_KEY=
```

El nombre `OLLAMA_URL` se conserva por compatibilidad, aunque también se utiliza para Groq.

## 8. Ejecución

Levantar FastAPI:

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000
```

Comprobar el estado:

```text
http://127.0.0.1:8000/
```

Respuesta esperada:

```json
{
  "status": "running",
  "service": "FinancIAl WhatsApp Bot",
  "version": "1.1.0-meta",
  "whatsapp_meta": "configured",
  "ollama": "connected (...)"
}
```

Exponer el puerto local:

```powershell
ngrok http 8000
```

La URL pública será similar a:

```text
https://subdominio.ngrok-free.dev
```

El callback debe ser:

```text
https://subdominio.ngrok-free.dev/webhook/whatsapp
```

Cuando cambia la URL gratuita de ngrok, hay que actualizar y volver a verificar el callback en Meta. No es necesario repetir la suscripción de la aplicación a la WABA.

## 9. Configuración de Meta

### 9.1 Portfolio y aplicación

Configuración actual:

- Portfolio comercial: `Financial Chile USM`.
- Aplicación: `Financial Chat Bot`.
- Producto: WhatsApp Business Platform.

La aplicación se administra desde:

- Meta for Developers: <https://developers.facebook.com/apps/>
- WhatsApp Manager: <https://business.facebook.com/wa/manage/>
- Graph API Explorer: <https://developers.facebook.com/tools/explorer/>

### 9.2 Webhook

En Meta:

```text
Aplicación
→ WhatsApp o Webhooks
→ WhatsApp Business Account
```

Configurar:

```text
Callback URL: https://URL-PUBLICA/webhook/whatsapp
Verify token: mismo valor de META_WEBHOOK_VERIFY_TOKEN
```

El campo `messages` debe aparecer como suscrito.

La URL del callback no debe incluir manualmente parámetros como:

```text
hub_mode
hub_challenge
hub_verify_token
```

Meta agrega automáticamente:

```text
hub.mode
hub.challenge
hub.verify_token
```

### 9.3 Suscribir la aplicación a la WABA

Esta operación se realiza una vez por combinación de aplicación y WABA.

En Graph API Explorer:

1. Seleccionar `Financial Chat Bot`.
2. Generar un token con `whatsapp_business_management` y `whatsapp_business_messaging`.
3. Seleccionar el método `POST`.
4. Ejecutar:

```text
/{META_WABA_ID}/subscribed_apps
```

Respuesta esperada:

```json
{
  "success": true
}
```

Verificar con:

```text
GET /{META_WABA_ID}/subscribed_apps
```

La lista debe contener `Financial Chat Bot`. La aplicación interna `WA DevX Webhook Events 1P App` puede seguir apareciendo y no representa un problema.

No es necesario repetir esta operación al reiniciar Uvicorn, cambiar el token o cambiar la URL de ngrok. Debe repetirse si se utiliza otra aplicación o una WABA distinta.

### 9.4 Número de prueba

Durante desarrollo, Meta permite enviar mensajes solo a destinatarios agregados y verificados en la configuración de la API.

El botón de prueba de webhooks utiliza un teléfono simulado. Si el backend intenta responderle, Meta puede devolver:

```text
(#131030) Recipient phone number not in allowed list
```

Eso no indica un error del backend. Para una prueba real se debe responder desde un teléfono agregado a la lista de destinatarios.

### 9.5 Número definitivo

Para registrar un número nuevo:

```text
Aplicación
→ Conectar en WhatsApp
→ Configuración básica
→ Paso 2. Configuración de producción
→ Registrar número de teléfono
→ Añadir número nuevo
```

Requisitos:

- Número dedicado capaz de recibir SMS o llamadas.
- Nombre visible coherente con el proyecto o institución.
- Verificación mediante código.
- Método de pago para mensajes iniciados por la empresa.
- Token permanente para producción.

Si el número se agrega a la misma WABA, normalmente solo cambia:

```env
META_PHONE_NUMBER_ID=nuevo_identificador
```

Si Meta crea otra WABA, también se debe actualizar `META_WABA_ID` y repetir `POST /{WABA_ID}/subscribed_apps`.

## 10. Flujo del webhook

### 10.1 Verificación inicial

Endpoint:

```text
GET /webhook/whatsapp
```

Meta envía `hub.mode`, `hub.verify_token` y `hub.challenge`. El backend compara el token y devuelve el challenge como texto plano.

Resultados:

- `200`: token correcto.
- `403`: token incorrecto, ausente o no cargado.

Después de modificar `.env`, se debe reiniciar Uvicorn porque el recargado automático no siempre detecta cambios de configuración.

### 10.2 Recepción de eventos

Endpoint:

```text
POST /webhook/whatsapp
```

El backend:

1. Lee el cuerpo original.
2. Valida `X-Hub-Signature-256` usando `META_APP_SECRET`.
3. Comprueba que el objeto sea `whatsapp_business_account`.
4. Registra estados como `sent`, `delivered`, `read` y `failed`.
5. Ignora temporalmente eventos duplicados mediante el ID `wamid`.
6. Normaliza el teléfono al formato `+código_país...`.
7. Extrae texto, botones o respuestas de listas.
8. Ejecuta `route_message()`.
9. Envía la respuesta por Graph API.
10. Devuelve HTTP 200 a Meta.

Los mensajes multimedia todavía no se procesan. El bot responde solicitando una consulta de texto.

### 10.3 Seguridad

Cada `POST` debe incluir una firma válida. El cálculo se realiza mediante HMAC SHA-256 con `META_APP_SECRET` y comparación segura con `hmac.compare_digest()`.

Sin una firma válida, el endpoint responde `401`.

## 11. Flujo de negocio

### 11.1 Creación del usuario

Cuando llega un teléfono desconocido:

```python
{"phone": phone, "onboarding_step": 0}
```

El usuario se crea mediante un `upsert` por teléfono.

### 11.2 Onboarding

Pasos:

1. Presentación.
2. Detección del rubro.
3. Registro de comuna.
4. Consulta de formalización ante el SII.
5. Generación del roadmap.

Rubros detectados explícitamente:

- Textil.
- Alimentos.
- Joyería.
- Otro.

Estados de onboarding:

```text
0 → 1 → 2 → 3 → done
```

### 11.3 Comandos

| Entrada | Acción |
|---|---|
| `roadmap`, `mi roadmap`, `hitos`, `qué me falta`, `mis pasos`, `mi ruta` | Muestra el roadmap. |
| `listo`, `hecho`, `completado`, `ya lo hice`, `siguiente` | Completa el primer hito pendiente. |
| `fondo`, `postular`, `capital semilla`, `sercotec`, `corfo` | Simulación de fondos. |
| `ayuda`, `help`, `menú`, `opciones` | Menú de ayuda. |
| `reiniciar`, `reset`, `empezar de nuevo` | Reinicia el onboarding. |
| Cualquier otra consulta | Pasa al flujo de IA. |

### 11.4 Roadmaps

Cada roadmap es una lista JSON de hitos con:

```json
{
  "id": 1,
  "title": "Nombre del hito",
  "desc": "Descripción",
  "done": false
}
```

El comando `listo` marca el primer hito pendiente y muestra el siguiente.

## 12. Inteligencia artificial

### 12.1 Proveedores

El código puede utilizar:

- Groq Cloud cuando `IA_API_KEY` tiene valor.
- Ollama o un servidor compatible cuando `IA_API_KEY` está vacío.

La solicitud utiliza el formato OpenAI Chat Completions.

### 12.2 Prompt

El prompt considera:

- Rubro.
- Comuna.
- Estado SII.
- Progreso del roadmap.
- Resumen de conversaciones anteriores.
- Contexto recuperado desde documentos municipales.

La respuesta está diseñada para WhatsApp: breve, en español chileno y sin inventar información normativa.

### 12.3 Memoria

Para las respuestas de IA se recuperan los últimos seis mensajes.

Cada diez mensajes persistidos se genera un resumen utilizando hasta los últimos cincuenta mensajes. El resumen se guarda en `users.resumen_conversacion`.

## 13. RAG

El RAG utiliza:

- Modelo `intfloat/multilingual-e5-base`.
- Embeddings de 768 dimensiones.
- PostgreSQL con extensión `vector`.
- Operador de distancia coseno de pgvector.

Comunas actualmente soportadas:

- Recoleta.
- El Bosque.

La consulta recupera hasta cuatro documentos relevantes, filtrando por comuna o contenido general.

Si una comuna no está soportada, no consulta la base de datos. Si la comuna está soportada pero no se encuentra información, el prompt instruye al modelo para derivar al usuario a la municipalidad.

### 13.1 Ingesta documental

El script `RAG/ingest_supabase.py`:

1. Lee archivos Markdown desde `RAG/data`.
2. Limpia y genera metadata.
3. Divide el contenido en fragmentos.
4. Genera embeddings con prefijo `passage:`.
5. Crea la tabla `documents` y el índice vectorial si no existen.
6. Inserta contenido, metadata y embedding.

La consulta utiliza el prefijo `query:` para las preguntas.

## 14. Persistencia de usuarios y mensajes

La lógica está centralizada principalmente en `financial-whatsapp-bot/db/users.py`.

Funciones:

| Función | Uso |
|---|---|
| `get_user(phone)` | Obtiene un perfil por teléfono. |
| `get_user_id(phone)` | Obtiene el UUID interno del usuario. |
| `save_user(phone, data)` | Crea o actualiza el perfil. |
| `save_message(phone, role, content, channel)` | Guarda un mensaje asociado al UUID del usuario. |
| `get_messages(phone, limit)` | Recupera mensajes cronológicamente. |
| `contar_mensajes(phone)` | Cuenta mensajes del usuario. |

### 14.1 Tabla `users`

Campos utilizados:

```text
id
phone
auth_user_id
rubro
rubro_raw
comuna
inicio_sii
onboarding_step
roadmap
created_at
updated_at
resumen_conversacion
```

`phone` funciona como identidad de WhatsApp y clave de conflicto para `upsert`. `roadmap` se envía directamente como JSONB.

### 14.2 Tabla `messages`

Campos utilizados:

```text
id
user_id
phone
role
content
channel
created_at
```

Los roles esperados son `user` y `assistant`. El canal predeterminado es `whatsapp`.

La base debe generar automáticamente UUID y fechas cuando el backend no los envía.

### 14.3 Fallback local

Si Supabase no está configurado, los perfiles se almacenan temporalmente en memoria. Los mensajes no cuentan con persistencia local, por lo que se pierden.

## 15. Endpoints

| Método | Ruta | Uso |
|---|---|---|
| `GET` | `/` | Estado de la aplicación. |
| `GET` | `/webhook/whatsapp` | Verificación del webhook de Meta. |
| `POST` | `/webhook/whatsapp` | Mensajes y estados de WhatsApp. |
| `GET` | `/test/chat` | Interfaz local de prueba. |
| `POST` | `/test/chat` | Simulación de una conversación sin llamar a Meta. |
| `GET` | `/test/rag-database?q=...` | Prueba directa de recuperación vectorial. |
| `GET` | `/test/resumen/{phone}` | Consulta los últimos mensajes del usuario. |
| `GET` | `/internal/reminders/run` | Ejecución de Vercel Cron con `Authorization: Bearer CRON_SECRET`. |
| `POST` | `/internal/reminders/run` | Ejecución manual con la misma autorización. |

Los endpoints de prueba no deberían exponerse públicamente sin protección en producción.

## 16. Envío de mensajes

`services/whatsapp.py` construye el endpoint:

```text
https://graph.facebook.com/{VERSION}/{PHONE_NUMBER_ID}/messages
```

### 16.1 Texto libre

`send_text(phone, content)` se utiliza dentro de la ventana de atención iniciada por el usuario.

### 16.2 Plantillas

`send_template(phone, template_name, language_code, parameters)` permite enviar una plantilla aprobada.

Los mensajes proactivos fuera de la ventana de atención deben utilizar una plantilla aprobada por Meta.

## 17. Recordatorios proactivos

La funcionalidad está implementada con consentimiento explícito, cálculo de
inactividad, tres intentos, pausa automática y persistencia en
`reminder_deliveries`. Los recordatorios 1 y 2 usan
`recordatorio_roadmap`; el tercero usa `last_reminder_roadmap`.

La rutina se ejecuta mediante:

```text
GET /internal/reminders/run
Authorization: Bearer CRON_SECRET
```

El mismo endpoint acepta `POST` para pruebas manuales. `vercel.json` programa
la llamada diaria a las 13:00 UTC. En producción deben configurarse
`REMINDERS_ENABLED=true`, `SUPABASE_SERVICE_ROLE_KEY`, las plantillas y un
`CRON_SECRET` largo en las variables de entorno de Vercel.

## 18. Despliegue

El `Procfile` contiene:

```text
web: uvicorn main:app --host 0.0.0.0 --port $PORT
```

Para producción se requiere:

- URL HTTPS estable.
- Variables de entorno configuradas en el hosting.
- Token permanente de Meta.
- Número definitivo registrado.
- Método de pago.
- Endpoints de prueba restringidos o eliminados.
- Logs y monitoreo.
- `CRON_SECRET` configurado en Vercel.
- Cron diario visible en **Settings → Cron Jobs**.

Al desplegar más de un proceso, la deduplicación en memoria del webhook deja de ser suficiente.

## 19. Seguridad

- No subir `.env`.
- No registrar tokens completos en logs.
- Rotar cualquier secreto expuesto accidentalmente.
- Utilizar token permanente de System User en producción.
- Validar siempre `X-Hub-Signature-256`.
- Mantener `META_WEBHOOK_VERIFY_TOKEN` diferente de `META_APP_SECRET` y del token de acceso.
- Utilizar una clave Supabase apropiada para backend y políticas RLS correctas.
- Proteger los endpoints `/test/*`.
- Mantener HTTPS.
- Almacenar consentimiento para comunicaciones proactivas.

## 20. Problemas frecuentes

### Meta no valida el callback y FastAPI responde 403

Revisar:

- Callback sin parámetros manuales.
- Mismo `META_WEBHOOK_VERIFY_TOKEN` en Meta y `.env`.
- Reinicio de Uvicorn después de modificar `.env`.
- URL actual de ngrok.

### El botón de prueba funciona, pero los mensajes reales no llegan

Comprobar:

```text
GET /{WABA_ID}/subscribed_apps
```

Si no aparece `Financial Chat Bot`, ejecutar una vez:

```text
POST /{WABA_ID}/subscribed_apps
```

También confirmar que `messages` esté suscrito.

### Graph API responde `Unknown path components`

Revisar que la versión use `v` minúscula:

```env
META_GRAPH_API_VERSION=v26.0
```

### Error `(#131030) Recipient phone number not in allowed list`

En modo de prueba, agregar y verificar el destinatario. Si el evento provino del botón de prueba de webhooks, el teléfono puede ser ficticio y no se puede responder.

### Webhook POST responde 401

Revisar que `META_APP_SECRET` sea la clave secreta de la misma aplicación que tiene configurado el webhook.

### Meta recibe el mensaje pero no responde

Revisar en este orden:

1. `POST /webhook/whatsapp` en ngrok.
2. Estado HTTP del webhook.
3. Error de Graph API en Uvicorn.
4. Vigencia del token.
5. `META_PHONE_NUMBER_ID`.
6. Lista de destinatarios autorizados en modo de prueba.

### No responde la IA

Revisar:

- `IA_API_KEY`.
- `OLLAMA_URL`.
- `OLLAMA_MODEL`.
- Conectividad con Groq u Ollama.
- Carga del modelo de embeddings.
- `DB_DSN` para el RAG.

## 21. Limitaciones y deuda técnica

- La deduplicación de webhooks es solo en memoria y conserva hasta 10.000 IDs recientes.
- Con varios workers o reinicios puede procesarse nuevamente un webhook repetido.
- Los estados de los recordatorios y su `wamid` se guardan en
  `reminder_deliveries`; otros mensajes todavía no tienen esta trazabilidad.
- Los mensajes multimedia no se procesan.
- Vercel no reintenta automáticamente una ejecución fallida del cron.
- Una entrega interrumpida justo después del envío puede quedar en estado
  `pending` y requiere una estrategia de recuperación para mayor robustez.
- El token temporal debe sustituirse por uno permanente.
- Los endpoints de prueba están públicos si no se protegen en despliegue.
- Algunas operaciones de Supabase usan un cliente síncrono y se ejecutan en threads desde el webhook.
- La conexión RAG dentro de `core/ia.py` abre una conexión PostgreSQL directa por consulta en vez de reutilizar el pool inicializado.
- La actualización de `resumen_conversacion` todavía se realiza directamente desde `core/ia.py`, fuera de `db/users.py`.

## 22. Lista de comprobación para producción

- [ ] Registrar y verificar número definitivo.
- [ ] Confirmar aprobación del nombre visible.
- [ ] Configurar método de pago.
- [ ] Generar token permanente.
- [ ] Actualizar `META_PHONE_NUMBER_ID`.
- [ ] Confirmar la WABA correcta.
- [ ] Confirmar `Financial Chat Bot` en `subscribed_apps`.
- [ ] Confirmar suscripción al campo `messages`.
- [ ] Usar URL HTTPS estable.
- [ ] Configurar secretos en el hosting.
- [ ] Configurar `CRON_SECRET` en Vercel.
- [ ] Confirmar `/internal/reminders/run` en **Settings → Cron Jobs**.
- [ ] Revisar logs y una ejecución controlada del cron.
- [ ] Crear plantillas de recordatorio.
- [ ] Registrar consentimiento del usuario.
- [ ] Proteger endpoints de prueba.
- [ ] Implementar deduplicación persistente.
- [ ] Implementar monitoreo de errores y estados de entrega.
- [ ] Ejecutar pruebas completas de onboarding, roadmap, IA y RAG.

## 23. Prueba funcional mínima

1. Iniciar Uvicorn.
2. Iniciar ngrok o utilizar la URL pública.
3. Confirmar `/` con estado `running`.
4. Confirmar callback y suscripción `messages` en Meta.
5. Enviar `hola` desde WhatsApp.
6. Verificar `POST /webhook/whatsapp 200`.
7. Completar onboarding.
8. Ejecutar `mi roadmap`.
9. Ejecutar `listo`.
10. Realizar una consulta de IA.
11. Verificar usuario y mensajes en Supabase.
12. Revisar que Meta registre estados de entrega.

---

Documento preparado a partir del estado actual del repositorio. No contiene valores secretos del archivo `.env`.
