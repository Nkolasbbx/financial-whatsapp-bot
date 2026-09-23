import asyncio
import logging
import os

from arq import cron

import dependencies
from config import ARQ_QUEUE_NAME, INGESTION_JOB_TIMEOUT_SECONDS
from core.ia import process_ai_and_send
from core.ingestion import embed_batch_remoto, extract_pdf_to_markdown, process_document_to_rows
from db.documents import get_stale_ingestion_jobs, update_ingestion_job, upsert_document
from phone_lock import release_phone_lock
from redis_settings import get_redis_settings
from services.alertas_tributarias import send_tax_alerts
from services.calendar_reminders import send_due_calendar_reminders
from services.financial_movements import process_financial_movement_and_send

from services.ingestion_jobs import STORAGE_BUCKET
from services.reminders import send_due_reminders

logger = logging.getLogger("financial.worker")

REDIS_SETTINGS = get_redis_settings()


async def startup(ctx):
    """Hook de arq: el worker corre en un proceso aparte del servidor web, así
    que no pasa por el lifespan de FastAPI y necesita inicializar sus propias
    dependencias compartidas (Supabase, pool de Postgres, etc.) al arrancar.

    Si falta Postgres o Supabase admin se aborta el arranque: un worker así
    igual tomaría jobs de la cola y los haría fallar al instante (sin poder
    siquiera marcarlos como fallidos), robándoselos a un worker sano.
    """
    await dependencies.init_dependencies()
    faltantes = [
        nombre
        for nombre, valor in (
            ("pool de Postgres (DB_DSN)", dependencies.db_pool),
            ("cliente admin de Supabase (SUPABASE_SERVICE_ROLE_KEY)", dependencies.supabase_admin),
        )
        if valor is None
    ]
    if faltantes:
        await dependencies.shutdown_dependencies()
        raise RuntimeError(
            "El worker no puede arrancar sin: " + ", ".join(faltantes) + ". Revisa las variables de entorno."
        )
    logger.info("Worker escuchando la cola %s", ARQ_QUEUE_NAME)


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


async def process_financial_movement_task(
    ctx,
    phone: str,
    message: str,
    lock_token: str | None = None,
):
    """Interpreta un movimiento sin bloquear el webhook de WhatsApp."""
    logger.info("Procesando movimiento financiero para %s", phone)
    try:
        await process_financial_movement_and_send(phone, message)
    except Exception:
        logger.exception("Fallo procesando movimiento financiero para %s", phone)
        raise
    finally:
        if lock_token is not None:
            await release_phone_lock(ctx["redis"], phone, lock_token)


async def run_reminders_job(ctx):
    """Cron de arq: dispara recordatorios y alertas tributarias/de fondos.

    Reemplaza al trigger externo (Vercel Cron) que llamaba a
    /internal/reminders/run: como el worker ya corre 24/7 con las mismas
    dependencias, no hace falta un salto HTTP ni CRON_SECRET. Se traga
    cualquier excepción para que un fallo acá no tumbe el worker ni afecte
    el procesamiento de mensajes de WhatsApp.
    """
    results = {}
    services = (
        ("roadmap", send_due_reminders),
        ("alerts", send_tax_alerts),
        ("calendar", send_due_calendar_reminders),
    )
    for name, service in services:
        try:
            results[name] = await service()
        except Exception:
            logger.exception("Fallo ejecutando el servicio de %s", name)
            results[name] = {"status": "failed"}

    logger.info("Cron de recordatorios ejecutado: %s", results)


async def process_document_ingestion_task(
    ctx,
    job_id: str,
    storage_path: str,
    file_name: str,
    content_type: str,
    comuna: str,
    rubros: list[str],
    vigencia_desde: str | None,
    vigencia_hasta: str | None,
    content_hash: str,
    uploaded_by: str,
):
    """Job de arq (HdU15): descarga el documento subido desde el panel admin,
    lo convierte a filas parent-child con embeddings, y las inserta de forma
    incremental en `documents`. Corre en background para que el endpoint de
    subida (routers/admin.py) no bloquee esperando el procesamiento (AC1).

    La extracción (PDF->markdown) y el chunking son CPU-bound y síncronos:
    se corren en un hilo aparte (asyncio.to_thread) para no bloquear el
    event loop del worker mientras se procesan otros jobs (ej. mensajes de
    WhatsApp en process_ai_task).
    """
    from datetime import datetime, timezone

    logger.info("Procesando ingesta de documento %s (job %s)", file_name, job_id)
    update_ingestion_job(job_id, status="processing")

    try:
        raw_bytes = dependencies.supabase_admin.storage.from_(STORAGE_BUCKET).download(storage_path)

        flagged_pages: list[dict] = []
        if file_name.lower().endswith(".pdf"):
            tmp_path = f"/tmp/ingestion_{job_id}.pdf"
            with open(tmp_path, "wb") as f:
                f.write(raw_bytes)
            try:
                text, flagged_pages = await asyncio.to_thread(extract_pdf_to_markdown, tmp_path)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            file_type = "pdf"
        else:
            text = raw_bytes.decode("utf-8")
            file_type = "markdown"

        extra_meta = {
            "comuna": comuna,
            "rubros": rubros,
            "vigencia_desde": vigencia_desde,
            "vigencia_hasta": vigencia_hasta,
            "content_hash": content_hash,
            "uploaded_by": uploaded_by,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "source": f"Panel admin — {uploaded_by}",
            "source_url": "",
            "source_date": datetime.now(timezone.utc).date().isoformat(),
            "review_flag": bool(flagged_pages),
        }

        rows = await asyncio.to_thread(process_document_to_rows, file_name, text, file_type, extra_meta)

        if rows:
            embed_texts = [row["embed_text"] for row in rows]
            vectors = await embed_batch_remoto(embed_texts, prefix="passage")
            for row, vector in zip(rows, vectors):
                row["embedding"] = vector

        chunks_written = upsert_document(file_name, rows)

        update_ingestion_job(
            job_id,
            status="done",
            chunks_written=chunks_written,
            review_flag=bool(flagged_pages),
            review_details={"flagged_pages": flagged_pages} if flagged_pages else None,
        )
        logger.info("Ingesta completada: %s -> %d chunks (job %s)", file_name, chunks_written, job_id)
    except Exception as exc:
        logger.exception("Fallo la ingesta de %s (job %s)", file_name, job_id)
        try:
            update_ingestion_job(job_id, status="failed", error_message=str(exc)[:2000])
        except Exception:
            # Si esto también falla el job queda en 'queued'/'processing' hasta
            # que cleanup_orphaned_uploads_job lo marque como fallido.
            logger.exception("No se pudo marcar como fallido el job %s", job_id)
        raise  # re-lanzar: arq registra el job como fallido (no lo reintenta: solo reintenta con Retry o timeouts)
    finally:
        try:
            dependencies.supabase_admin.storage.from_(STORAGE_BUCKET).remove([storage_path])
        except Exception:
            logger.warning("No se pudo limpiar el objeto temporal de Storage: %s", storage_path)


STALE_INGESTION_JOB_MINUTES = 60


async def cleanup_orphaned_uploads_job(ctx):
    """Cron de arq (HdU15, parte 2): red de seguridad para el free tier de
    Supabase Storage (1GB total, ver docs/ingestion.md). Si el worker muere a
    mitad de process_document_ingestion_task (kill forzado, OOM, o una
    cancelación por timeout de arq que no pasa por el `except Exception` del
    job), ese job queda atascado en 'queued'/'processing' para siempre y su
    archivo temporal puede quedar huérfano en el bucket. Acá se detectan esos
    jobs (más de STALE_INGESTION_JOB_MINUTES sin actualizarse), se reintenta
    borrar su archivo de Storage (no-op si ya no existe) y se marcan como
    'failed' para que dejen de verse "Procesando" para siempre en el panel.
    """
    stale_jobs = get_stale_ingestion_jobs(STALE_INGESTION_JOB_MINUTES)
    for job in stale_jobs:
        storage_path = f"{job['content_hash']}/{job['file_name']}"
        try:
            dependencies.supabase_admin.storage.from_(STORAGE_BUCKET).remove([storage_path])
        except Exception:
            logger.warning("No se pudo limpiar el objeto huérfano de Storage: %s", storage_path)
        update_ingestion_job(
            job["id"],
            status="failed",
            error_message="Job huérfano: el worker no terminó de procesarlo (posible caída o reinicio).",
        )

    if stale_jobs:
        logger.info("Limpieza de ingesta huérfana: %d job(s) marcados como fallidos", len(stale_jobs))


class WorkerSettings:

    functions = [process_ai_task, process_document_ingestion_task, process_financial_movement_task]
    cron_jobs = [
        cron(run_reminders_job, hour=set(range(24)), minute=0, run_at_startup=False),
        cron(cleanup_orphaned_uploads_job, hour=set(range(24)), minute=30, run_at_startup=False),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = REDIS_SETTINGS
    queue_name = ARQ_QUEUE_NAME
    max_jobs = 10
    job_timeout = INGESTION_JOB_TIMEOUT_SECONDS
    max_tries = 3
    retry_jobs = True
