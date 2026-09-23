"""
FinancIAl — services/ingestion_jobs.py

Orquesta la subida de un documento desde el panel admin (HdU15): sube el
archivo a Supabase Storage (no hay filesystem compartido entre el servicio
web y el worker de arq en Railway), crea el registro de auditoría en
`ingestion_jobs`, y encola el job que realmente lo procesa
(worker.py::process_document_ingestion_task).

El endpoint que llama a esto (routers/admin.py) responde apenas termina
enqueue_document_ingestion — el procesamiento pesado ocurre después, en el
worker, sin bloquear el request (AC1 de la HdU).
"""
import logging
import uuid

from config import (
    INGESTION_MAX_BATCH_FILES,
    INGESTION_MAX_BATCH_MB,
    INGESTION_MAX_UPLOAD_MB,
    INGESTION_STORAGE_BUCKET,
)
from db.documents import compute_content_hash, create_ingestion_job

logger = logging.getLogger("financial")

MAX_UPLOAD_BYTES = INGESTION_MAX_UPLOAD_MB * 1024 * 1024
MAX_BATCH_BYTES = INGESTION_MAX_BATCH_MB * 1024 * 1024
ALLOWED_EXTENSIONS = (".pdf", ".md")
STORAGE_BUCKET = INGESTION_STORAGE_BUCKET


async def enqueue_document_ingestion(
    redis,
    raw_bytes: bytes,
    file_name: str,
    content_type: str,
    comuna: str,
    uploaded_by: str,
    rubros: list[str],
    vigencia_desde: str | None,
    vigencia_hasta: str | None,
) -> str:
    """Sube el archivo a Storage, registra el job en Postgres y lo encola en
    arq. Devuelve el job_id (uuid) para que el panel pueda hacer polling."""
    import dependencies

    content_hash = compute_content_hash(raw_bytes)
    storage_path = f"{content_hash}/{file_name}"

    dependencies.supabase_admin.storage.from_(STORAGE_BUCKET).upload(
        storage_path,
        raw_bytes,
        {"content-type": content_type or "application/octet-stream", "upsert": "true"},
    )

    job_id = str(uuid.uuid4())
    create_ingestion_job(
        job_id=job_id,
        file_name=file_name,
        content_hash=content_hash,
        comuna=comuna,
        rubros=rubros,
        vigencia_desde=vigencia_desde,
        vigencia_hasta=vigencia_hasta,
        uploaded_by=uploaded_by,
    )

    await redis.enqueue_job(
        "process_document_ingestion_task",
        job_id=job_id,
        storage_path=storage_path,
        file_name=file_name,
        content_type=content_type,
        comuna=comuna,
        rubros=rubros,
        vigencia_desde=vigencia_desde,
        vigencia_hasta=vigencia_hasta,
        content_hash=content_hash,
        uploaded_by=uploaded_by,
        _job_id=job_id,
    )

    logger.info(
        "Ingesta encolada: %s (comuna=%s, job_id=%s)", file_name, comuna, job_id
    )
    return job_id


def validate_upload(file_name: str, content_type: str | None, size: int) -> str | None:
    """Valida tipo y tamaño del archivo subido. Devuelve un mensaje de error
    o None si es válido."""
    if size > MAX_UPLOAD_BYTES:
        return f"El archivo supera el tamaño máximo permitido ({INGESTION_MAX_UPLOAD_MB} MB)."

    name_lower = (file_name or "").lower()
    if not name_lower.endswith(ALLOWED_EXTENSIONS):
        return "Solo se aceptan archivos PDF (.pdf) o Markdown (.md)."

    return None


def validate_batch(files: list[tuple[str, int]]) -> str | None:
    """Valida un lote completo de (nombre, tamaño). Devuelve un mensaje de
    error o None si es válido. Un lote que falla acá se rechaza entero."""
    if not files:
        return "Selecciona al menos un archivo."

    if len(files) > INGESTION_MAX_BATCH_FILES:
        return f"Máximo {INGESTION_MAX_BATCH_FILES} archivos por subida (elegiste {len(files)})."

    if sum(size for _, size in files) > MAX_BATCH_BYTES:
        return f"El total de la subida supera el máximo permitido ({INGESTION_MAX_BATCH_MB} MB)."

    # upsert_document reemplaza por file_name: dos archivos con el mismo
    # nombre en un lote se pisarían entre sí.
    nombres = [name.lower() for name, _ in files]
    repetidos = sorted({n for n in nombres if nombres.count(n) > 1})
    if repetidos:
        return f"Hay archivos con el mismo nombre en la subida: {', '.join(repetidos)}."

    return None
