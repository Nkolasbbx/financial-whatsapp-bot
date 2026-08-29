import logging

import dependencies
from core.ia import process_ai_and_send
from phone_lock import release_phone_lock
from redis_settings import get_redis_settings

logger = logging.getLogger("financial.worker")

REDIS_SETTINGS = get_redis_settings()


async def startup(ctx):
    """Hook de arq: el worker corre en un proceso aparte del servidor web, así
    que no pasa por el lifespan de FastAPI y necesita inicializar sus propias
    dependencias compartidas (Supabase, pool de Postgres, etc.) al arrancar."""
    await dependencies.init_dependencies()


async def shutdown(ctx):
    await dependencies.shutdown_dependencies()


async def process_ai_task(
    ctx,
    phone: str,
    message: str,
    hito_context: dict | None = None,
    reformulate_mode: bool = False,
    lock_token: str | None = None,
):
    """Job que ejecuta el worker: procesa la IA y envía la respuesta por WhatsApp.

    lock_token viene del lock de orden de respuesta que tomó el proceso web
    al encolar este job (ver acquire_phone_lock en routers/webhook.py). Acá
    se libera pase lo que pase, para que el siguiente mensaje de este mismo
    teléfono no se responda antes de que esta respuesta termine de enviarse.
    """
    logger.info("Procesando job IA para %s (reformulate=%s)", phone, reformulate_mode)
    try:
        await process_ai_and_send(
            phone,
            message,
            dependencies.ollama_available,
            hito_context=hito_context,
            reformulate_mode=reformulate_mode,
        )
    except Exception:
        logger.exception("Fallo procesando tarea IA para %s", phone)
        raise  # re-lanzar: así arq marca el job como fallido y lo reintenta
    finally:
        if lock_token is not None:
            await release_phone_lock(ctx["redis"], phone, lock_token)


class WorkerSettings:
    functions = [process_ai_task]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = REDIS_SETTINGS
    max_jobs = 10
    job_timeout = 120
    max_tries = 3
    retry_jobs = True