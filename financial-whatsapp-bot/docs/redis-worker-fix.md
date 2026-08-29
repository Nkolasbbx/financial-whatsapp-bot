# Redis: arquitectura, bug y fix (proceso worker sin dependencias inicializadas)

## Contexto

El bot corre en **dos procesos separados**:

| Proceso | Comando | Rol |
|---|---|---|
| Web (uvicorn/FastAPI) | `make run-server` | Recibe el webhook de Meta, valida, clasifica el mensaje y **encola** el trabajo pesado (la consulta a la IA) en Redis. |
| Worker (arq) | `make run-worker` | Toma jobs de la cola de Redis y ejecuta la lógica de IA + envío de la respuesta por WhatsApp. |

Redis se usa acá para **dos cosas distintas**, que conviene no confundir:

1. **Deduplicación de mensajes entrantes de Meta** (`routers/webhook.py`).
2. **Cola de trabajos (job queue) con [arq](https://arq-docs.helpmanual.io/)** (`worker.py`, `dependencies.py`).

---

## 1. Deduplicación de mensajes (`_remember_message`)

Meta puede reenviar el mismo webhook si no confirma la recepción a tiempo. `routers/webhook.py:30-39`:

```python
async def _remember_message(redis, message_id: str) -> bool:
    was_set = await redis.set(
        f"msg_seen:{message_id}", "1", nx=True, ex=_MESSAGE_ID_TTL_SECONDS
    )
    return bool(was_set)
```

- Usa `SET NX EX` (set-if-not-exists con expiración) sobre la clave `msg_seen:<id>`.
- Es atómico: si dos webhooks llegan casi simultáneos para el mismo `message_id`, solo uno logra escribir la clave.
- TTL de 24h (`_MESSAGE_ID_TTL_SECONDS = 86400`), más que suficiente para descartar duplicados de Meta.
- Este uso vive **solo en el proceso web** — el worker nunca necesita esta conexión.

## 2. Cola de trabajos con arq

Cuando el webhook clasifica un mensaje como consulta de IA (`__AI_QUERY__`, `__AI_QUERY_WITH_CONTEXT__`, `__AI_QUERY_WITH_REFORMULATE__`), en vez de llamar a la IA ahí mismo, lo **encola**:

```python
await redis.enqueue_job("process_ai_task", phone, message)
```

`redis` acá es `request.app.state.redis`, un pool de conexión de **arq** (no un cliente Redis genérico) creado en `dependencies.py` durante el `lifespan` de FastAPI:

```python
redis_pool = await create_pool(RedisSettings.from_dsn(REDIS_URL))
app.state.redis = redis_pool
```

Del otro lado, `worker.py` define su **propia** conexión a Redis (independiente de `dependencies.redis_pool`) porque arq necesita administrar la cola desde el proceso worker:

```python
REDIS_SETTINGS = RedisSettings.from_dsn(REDIS_URL)

class WorkerSettings:
    functions = [process_ai_task]
    redis_settings = REDIS_SETTINGS
```

arq usa Redis como **broker**: serializa el job (nombre de función + argumentos) en una lista/stream de Redis; el worker hace polling/blocking sobre esa estructura y ejecuta la función correspondiente cuando aparece un job nuevo. Por eso el log que generó el reporte original se ve así:

```
12:42:55:   1.40s → 7c969e95f49746109a273a9f27364c36:process_ai_task('+56923628099', 'Hola') delayed=1.40s
No se encontró el usuario +56923628099 para responder con IA
12:42:55:   0.00s ← 7c969e95f49746109a273a9f27364c36:process_ai_task ●
```

- `→ ... delayed=1.40s`: arq recibió el job 1.40s después de que se encoló, y lo empezó a ejecutar. **Esto ya confirma que Redis funciona correctamente** como cola: el mensaje viajó del proceso web al proceso worker sin problema.
- `← ... ●` con `0.00s`: el job terminó rápido y **sin excepción** (si hubiera fallado, arq lo marca con un símbolo de error y lo reintenta según `max_tries`).
- La única señal de que algo salió mal es el `logger.warning` en medio — un problema *de aplicación*, no de infraestructura de colas.

---

## El bug: por qué el worker no encontraba al usuario

### Causa raíz

`dependencies.py` mantiene variables globales de módulo que representan clientes/pools compartidos: `supabase`, `supabase_admin`, `db_pool` (Postgres), `whatsapp_http_client`, `redis_pool`, `ollama_available`, etc.

Antes del fix, esas variables **solo se inicializaban dentro de `lifespan()`**, una función enganchada al ciclo de vida de FastAPI:

```python
app = FastAPI(title="FinancIAl WhatsApp Bot", lifespan=lifespan)
```

`lifespan()` se ejecuta cuando arranca **uvicorn** (`make run-server`). El worker (`make run-worker`) es un **proceso de Python completamente distinto** — arranca ejecutando `arq worker.WorkerSettings`, nunca importa ni ejecuta `main.py`, y por lo tanto nunca dispara `lifespan()`.

Consecuencia: en el proceso worker, `dependencies.supabase` se quedaba en `None` (su valor por defecto) durante toda su vida útil, **sin importar que las credenciales de Supabase estuvieran correctamente configuradas en `.env`** — el problema no era de configuración, era de que nadie llamaba al código que las usa.

### Cómo se propagaba hasta el log

1. El worker ejecuta `process_ai_task` → `process_ai_and_send(phone, message, ...)` (`core/ia.py`).
2. Esa función llama a `get_user(phone)` (`db/users.py`):
   ```python
   def get_user(phone: str) -> dict | None:
       import dependencies
       if dependencies.supabase:          # ← False en el worker, siempre
           ...consulta real a Supabase...
       return users_db.get(phone)          # ← fallback: dict en memoria VACÍO en este proceso
   ```
3. Como `dependencies.supabase` es `None`, se salta la consulta real y cae al diccionario en memoria `users_db`, que es propio de cada proceso (nunca se comparte entre el proceso web y el worker) y está vacío en el worker.
4. `get_user` devuelve `None` → `process_ai_and_send` hace `logger.warning("No se encontró el usuario %s para responder con IA", phone)` y retorna sin lanzar excepción → arq marca el job como exitoso (`●`, sin reintentos) aunque en la práctica no se respondió nada al usuario.

### Por qué no era un problema de Redis

- La cola encoló y entregó el job correctamente (`delayed=1.40s`, ejecución en `0.00s`).
- Redis nunca perdió, corrompió ni retrasó el mensaje.
- El fallo estaba completamente aguas abajo, en la falta de inicialización de dependencias del proceso worker.

---

## El fix

### 1. `dependencies.py` — separar inicialización de FastAPI

Se extrajo el cuerpo de `lifespan()` en dos funciones reutilizables, independientes de `FastAPI`/`asynccontextmanager`:

```python
async def init_dependencies() -> None:
    """Inicializa supabase, db_pool, redis_pool, whatsapp_http_client, etc."""
    ...  # mismo código que antes vivía en lifespan(), antes del yield

async def shutdown_dependencies() -> None:
    """Cierra lo que abrió init_dependencies()."""
    ...  # mismo código que antes vivía en lifespan(), después del yield

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Wrapper delgado, específico del proceso web."""
    await init_dependencies()
    app.state.redis = redis_pool   # solo el proceso web necesita esto
    yield
    await shutdown_dependencies()
```

`lifespan()` sigue siendo lo que usa `main.py`, con el mismo comportamiento de antes — cero cambios para el proceso web.

### 2. `worker.py` — enganchar la inicialización al ciclo de vida de arq

arq soporta los hooks `on_startup(ctx)` / `on_shutdown(ctx)`, invocados automáticamente al levantar y cerrar el worker:

```python
async def startup(ctx):
    await dependencies.init_dependencies()

async def shutdown(ctx):
    await dependencies.shutdown_dependencies()

class WorkerSettings:
    functions = [process_ai_task]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = REDIS_SETTINGS
    ...
```

Con esto, al ejecutar `make run-worker`, el proceso worker también termina con `dependencies.supabase`, `dependencies.db_pool`, `dependencies.whatsapp_http_client`, etc. correctamente inicializados — usando el mismo código que el proceso web, sin duplicar lógica.

### Qué globals le sirven al worker y cuáles no

| Global | ¿Lo usa el worker? | Detalle |
|---|---|---|
| `supabase` / `supabase_admin` | Sí | `get_user`, `save_user`, `save_message` (`db/users.py`) |
| `db_pool` (Postgres) | Sí | Búsqueda RAG (`obtener_contexto_rag` en `core/ia.py`) |
| `whatsapp_http_client` | Sí (opcional) | `services/whatsapp.py` ya tenía fallback: si es `None`, crea un `httpx.AsyncClient` temporal por request. Con el fix deja de hacerlo y reutiliza el cliente compartido, más eficiente. |
| `redis_pool` (de `dependencies.py`) | **No** | Es el pool que usa el proceso *web* para encolar jobs (`app.state.redis`). El worker administra su propia conexión Redis vía `WorkerSettings.redis_settings` — son dos conexiones con propósitos distintos, no hay que mezclarlas. |
| `ollama_available` | Se inicializa, pero no se consume | Se pasa como parámetro a `process_ai_and_send` / `get_ai_response`, pero hoy no se usa dentro de esa función. No es parte de este fix; queda como posible limpieza futura. |

---

## Cómo verificar

1. Levantar solo el worker (`make run-worker`) **sin** el servidor web corriendo — debe iniciar sin errores y loguear la inicialización de Supabase/Postgres/Redis igual que hace el proceso web.
2. Levantar ambos procesos, mandar un mensaje real de WhatsApp que dispare una consulta de IA.
3. Confirmar en los logs del worker que aparece `Respuesta de IA enviada a <phone>` en vez de `No se encontró el usuario ... para responder con IA`.
4. Confirmar que el usuario efectivamente recibe la respuesta en WhatsApp (no solo el log).
