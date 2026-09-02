# Orden de respuesta por teléfono (lock distribuido en Redis)

> Nota: este documento reemplaza el diseño anterior (lock en memoria con `asyncio.Lock`). Se cambió de mecanismo porque el bug real era más amplio de lo que el lock en memoria podía cubrir — ver la sección "Por qué el lock en memoria no alcanzaba".

## El bug reportado (segunda vuelta)

Con el lock en memoria ya implementado, seguía pasando esto: el usuario escribe *"hola"* (dispara una consulta de IA) y, casi enseguida, toca *"mi ruta de formalización"* (respuesta rápida). Orden recibido:

1. 🤔 Déjame pensar tu respuesta...
2. 📋 Tu Ruta de Formalización (el roadmap)
3. La respuesta de la IA a "hola"

Orden esperado: 1, luego la respuesta de la IA, luego el roadmap — es decir, **el orden en que el usuario disparó cada cosa**, sin importar cuál tardó más en resolverse.

## Por qué el lock en memoria no alcanzaba

El lock anterior (`asyncio.Lock` guardado en un diccionario del proceso web) solo protegía el tramo que corre **dentro del proceso web**. Pero una consulta de IA no termina ahí: el proceso web solo manda el "Déjame pensar..." y **encola** el job (`redis.enqueue_job("process_ai_task", ...)`) — la respuesta real la genera y la envía el **worker**, en otro proceso, minutos o segundos después.

Esto significa que el lock en memoria se liberaba apenas terminaba de encolar (algo casi instantáneo), sin ninguna relación con cuándo el worker terminaba de verdad. El segundo webhook (el del roadmap) llegaba, encontraba el lock libre, y se procesaba y enviaba de inmediato — sin ninguna señal de que "todavía hay una respuesta pendiente para este teléfono". Esto no era un problema de "falta escalar a más réplicas" (la limitación que sí tenía el diseño anterior): pasaba **incluso con una sola instancia de cada proceso**, porque el problema de fondo es que web y worker son procesos distintos con memoria distinta, y un `asyncio.Lock` no puede vivir ni liberarse entre procesos.

## El fix: lock en Redis, con traspaso (*handoff*) del proceso web al worker

Nuevo módulo `phone_lock.py`:

```python
async def acquire_phone_lock(redis, phone: str) -> str:
    key = f"phone_lock:{phone}"
    token = uuid.uuid4().hex
    while True:
        acquired = await redis.set(key, token, nx=True, ex=LOCK_TTL_SECONDS)
        if acquired:
            return token
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)

async def release_phone_lock(redis, phone: str, token: str) -> None:
    # Compara el token con un script Lua (get+del atómico) antes de borrar,
    # para no liberar el lock de otro holder si el nuestro ya expiró por TTL.
    ...
```

Como el lock vive en Redis (no en memoria de un proceso), **cualquier proceso puede tomarlo y cualquier otro puede liberarlo** — es justo lo que se necesita para el traspaso web → worker.

### Cómo queda el flujo en `routers/webhook.py`

```python
lock_token = await acquire_phone_lock(redis, phone)
hand_off_to_worker = False
try:
    ...
    if result == "__AI_QUERY__":
        await send_text(phone, "🤔 Déjame pensar tu respuesta...")
        await redis.enqueue_job("process_ai_task", phone, message, lock_token=lock_token)
        hand_off_to_worker = True   # el worker libera, no el web
    ...
    else:
        await _send_response(phone, result)   # camino rápido: se libera acá mismo
finally:
    if not hand_off_to_worker:
        await release_phone_lock(redis, phone, lock_token)
```

- **Camino rápido** (roadmap, menú, fondos, etc.): adquiere el lock, procesa y envía, libera el lock — todo dentro del mismo request, igual que antes.
- **Camino de IA**: adquiere el lock, manda el "Déjame pensar...", encola el job **pasándole el `lock_token`**, y sale del request **sin liberar** (`hand_off_to_worker = True`).

### Cómo queda el flujo en `worker.py`

```python
async def process_ai_task(ctx, phone, message, ..., lock_token=None):
    try:
        await process_ai_and_send(...)   # genera y envía la respuesta real
    finally:
        if lock_token is not None:
            await release_phone_lock(ctx["redis"], phone, lock_token)
```

El worker recibe el `redis` que ya trae armado en `ctx["redis"]` (arq lo puebla ahí antes de correr cualquier job) y libera el lock en un `finally`, así que se libera tanto si la respuesta se envió bien como si el job falló.

### Qué pasa con el segundo mensaje mientras tanto

El webhook del roadmap llega, llama a `acquire_phone_lock`, encuentra la key `phone_lock:<telefono>` ya tomada (por el job de IA en curso) y **espera con polling** (cada 0.3s) hasta que el worker la libere — recién ahí procesa y envía el roadmap. Se verificó esto con una simulación end-to-end contra el Redis real: una tarea de IA que "termina" 1 segundo después de empezar, seguida 0.1s después por el tap del roadmap — el orden final fue `['dejame pensar', 'respuesta IA a hola', 'roadmap']`, exactamente el orden esperado.

### Por qué es seguro esperar (bloquear) dentro del webhook

Bloquear una request de webhook mientras se espera un lock suena riesgoso, pero acá es seguro porque:
- Si Meta reintenta el webhook por tardanza, el reintento trae el mismo `message_id`, que `_remember_message` ya marcó como visto **antes** de llegar al lock — el reintento se descarta de inmediato, no se vuelve a encolar ni a procesar nada dos veces.
- Si la conexión de Meta se cierra mientras esperamos, el handler de FastAPI sigue corriendo igual (no depende de que el cliente siga conectado) — el roadmap se termina enviando por WhatsApp aunque la respuesta HTTP a Meta ya no le llegue a nadie.

### Por qué el TTL (`LOCK_TTL_SECONDS = 130`)

Un poco más que `job_timeout = 120` de `WorkerSettings`. Si el worker se cae o el job se cuelga sin liberar el lock, Redis lo expira solo — ningún teléfono queda bloqueado para siempre, ni siquiera ante un fallo total del worker.

## Limitación conocida (trade-off aceptado)

Con reintentos de arq (`max_tries=3`, `retry_jobs=True`): el lock se libera en el `finally` de **cada intento**, no solo al final de todos los reintentos. Si un job falla y arq lo reintenta, el teléfono queda momentáneamente libre durante ese hueco — un mensaje nuevo del mismo usuario podría colarse antes de que termine el reintento. Se aceptó este trade-off por simplicidad: es un caso raro (solo ocurre si el job falla), y sostener el lock a través de reintentos hubiese requerido pasar el mismo token entre intentos de forma más compleja.

## Por qué mejora las cosas

- Corrige la clase de bug real que se estaba viendo: una respuesta rápida ya no puede adelantarse a una respuesta de IA que se disparó antes, sin importar cuánto tarde el LLM.
- Funciona igual con una sola instancia de cada proceso o con varias réplicas del server web — a diferencia del lock en memoria, esto no dependía de "no escalar todavía": el problema era el cruce entre procesos web/worker, presente desde el día uno.
- El costo adicional en Redis es marginal frente al polling de arq (ver `docs/redis-resilience-and-cost.md`): un `SET NX EX` y un `EVAL` por mensaje, más algún `SET` extra si hay que esperar (polling cada 0.3s solo mientras el lock está tomado, no todo el tiempo).
