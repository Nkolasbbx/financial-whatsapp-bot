# Redis: timeouts endurecidos y costo de Upstash

Este documento cubre dos cambios relacionados, ambos en torno a la conexión a Redis: por qué el worker se caía con `TimeoutError`, y por qué dejarlo corriendo en producción contra Upstash puede salir caro si no se ajusta.

## 1. El bug: el worker moría ante cualquier hiccup de red

### Síntoma

```
redis.exceptions.TimeoutError: Timeout connecting to server
```

El worker procesaba un job normalmente (3.33s de ejecución), y al intentar escribir el resultado de vuelta en Redis (`finish_job` → `tr.execute()`), o al hacer polling de nuevos jobs (`_poll_iteration` → `zrangebyscore`), la conexión no se pudo (re)establecer a tiempo. Ese error **no se reintenta**: mata el `main_task` del worker, y arq termina el proceso completo.

### Causa raí (escribo esto para un push en develop)

`REDIS_URL` apunta a **Upstash** (`rediss://...upstash.io:6379`), un Redis remoto sobre TLS — no un `localhost`. `RedisSettings` de arq trae por defecto:

```python
conn_timeout: int = 1        # 1 segundo para abrir la conexión TCP+TLS
retry_on_timeout: bool = False
```

Un segundo es un margen muy ajustado para un handshake TCP+TLS contra un servidor en internet. Cualquier jitter de red (WiFi, ISP, el proceso local ocupado 3+ segundos corriendo la IA y sin tocar el socket de Redis mientras tanto) puede superarlo. Como `retry_on_timeout=False`, el cliente no reintenta — deja que la excepción se propague, y como esto ocurre en el loop principal del worker (no dentro del try/except de un job puntual), tumba el proceso entero.

### El fix: `redis_settings.py`

Se creó un único helper que arma el `RedisSettings` a partir de `REDIS_URL` y sobreescribe los valores por defecto:

```python
def get_redis_settings() -> RedisSettings:
    settings = RedisSettings.from_dsn(REDIS_URL)
    settings.conn_timeout = 5
    settings.retry_on_timeout = True
    settings.retry_on_error = [ConnectionError, TimeoutError]
    return settings
```

- `conn_timeout=5`: margen realista para TLS contra un servidor remoto, sin dejarlo tan alto que un Redis genuinamente caído tarde demasiado en reportarse.
- `retry_on_timeout=True` + `retry_on_error=[...]`: un timeout puntual se reintenta a nivel del cliente `redis-py` en vez de propagarse como excepción — el worker absorbe el blip en silencio en lugar de morir.

`RedisSettings.from_dsn()` **no acepta estos parámetros directamente** (solo parsea el DSN), por eso el helper construye el objeto y lo modifica después — es una dataclass mutable.

### Por qué se centralizó en un solo archivo

Antes, tanto `worker.py` como `dependencies.py` llamaban `RedisSettings.from_dsn(REDIS_URL)` cada uno por su cuenta — cualquier ajuste de timeout había que replicarlo en los dos lugares (y era fácil olvidar uno). Ahora ambos importan `get_redis_settings()` desde `redis_settings.py`:

- `worker.py`: `REDIS_SETTINGS = get_redis_settings()` → usado por `WorkerSettings.redis_settings`.
- `dependencies.py` (`init_dependencies()`): `redis_pool = await create_pool(get_redis_settings())` → usado por el proceso web para encolar jobs.

Una sola fuente de verdad para "cómo nos conectamos a Redis", consistente entre el proceso web y el worker.

### Por qué mejora las cosas

- El worker deja de crashear ante fluctuaciones normales de red contra un Redis remoto — se convierte en un problema silencioso y absorbido, no en una caída de proceso.
- El *auto-restart* de Railway (`restartPolicyType: ON_FAILURE`, ver `docs/railway-deploy.md`) sigue siendo la red de seguridad para fallos serios (Redis realmente caído por un rato largo), pero deja de ser la *primera línea de defensa* contra cualquier hipo de red — eso ahora se resuelve sin reiniciar nada.

---

## 2. El costo de dejar esto corriendo contra Upstash

### El hallazgo

arq hace *polling* a Redis cada `poll_delay=0.5s` por defecto, **mientras el worker esté prendido**, haya o no tráfico real:

```
2 polls/segundo × 86 400 seg/día × 30 días ≈ 5.18 millones de comandos/mes
```

Límites de Upstash (verificados en su página de precios):

| Plan | Límite / costo |
|---|---|
| Free | 500K comandos/mes |
| Pay as You Go | $0.20 por cada 100K comandos |

Solo el polling agota el plan Free en **~2-3 días** de worker corriendo, y en pay-as-you-go equivale a **~$10.4 USD/mes** antes de sumar un solo mensaje real de usuario, el job queue, la deduplicación de webhooks (`msg_seen:<id>`) o el lock de teléfono (ver `docs/message-ordering-lock.md`).

Esto es especialmente relevante para un **ambiente de dev en Railway**: a diferencia de la laptop (donde el worker se prende/apaga a mano), un servicio en Railway queda corriendo indefinidamente hasta que alguien lo pause explícitamente.

### Qué hacer al respecto

No se tocó código para esto — es una decisión de infraestructura, no de aplicación:

- **Recomendado**: mover Redis al propio Railway (ver `docs/railway-deploy.md`) — Railway cobra por el contenedor, no por comando, así que el polling deja de ser un problema de facturación.
- Alternativa si se sigue con Upstash: pasar a un plan Fixed (sin cobro por comando) o subir `poll_delay` en `WorkerSettings` (a costa de que un job tarde un poco más en empezar a procesarse tras encolarse).
