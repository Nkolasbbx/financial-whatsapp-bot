"""
FinancIAl — db/documents.py

Acceso a las tablas `documents` (RAG, pgvector) e `ingestion_jobs` (HdU15).
Usa psycopg2 crudo vía dependencies.db_pool, igual que core/ia.py::
obtener_contexto_rag, en vez del SDK de Supabase: `documents` necesita el
operador de pgvector (`<=>`) y casts (`::jsonb`, `::vector`) que el SDK no
expone.
"""
import hashlib
import json
import logging

from psycopg2.extras import RealDictCursor, execute_values

logger = logging.getLogger("financial")


def compute_content_hash(raw_bytes: bytes) -> str:
    """sha256 del archivo original (PDF o .md tal cual se subió), no del
    markdown ya extraído: así una re-subida idéntica del mismo archivo se
    detecta sin tener que re-extraer nada."""
    return hashlib.sha256(raw_bytes).hexdigest()


def delete_document_rows(cur, file_name: str) -> int:
    """Borra las filas existentes de `documents` para un documento (por
    nombre de archivo). Se llama siempre antes de insertar las filas nuevas
    de ESE documento, para que la ingesta sea incremental: una re-subida
    reemplaza solo sus propias filas, no toca otros documentos."""
    cur.execute("DELETE FROM documents WHERE metadata->>'file_name' = %s", (file_name,))
    return cur.rowcount


def insert_document_rows(cur, rows: list[dict]) -> int:
    """Inserta filas ya embebidas: cada row trae content/meta/embedding."""
    if not rows:
        return 0
    batch_data = [
        (row["content"], json.dumps(row["meta"], ensure_ascii=False), row["embedding"])
        for row in rows
    ]
    execute_values(
        cur,
        "INSERT INTO documents (content, metadata, embedding) VALUES %s",
        batch_data,
        template="(%s, %s::jsonb, %s::vector)",
        page_size=1000,
    )
    return len(batch_data)


def upsert_document(file_name: str, rows: list[dict]) -> int:
    """Reemplaza, en una sola transacción, todas las filas de `documents`
    correspondientes a `file_name` por las filas nuevas de `rows`. Si algo
    falla a mitad de camino, se hace rollback y no queda el documento borrado
    a medias."""
    import dependencies

    conn = dependencies.db_pool.getconn()
    try:
        with conn.cursor() as cur:
            delete_document_rows(cur, file_name)
            inserted = insert_document_rows(cur, rows)
        conn.commit()
        return inserted
    except Exception:
        conn.rollback()
        raise
    finally:
        dependencies.db_pool.putconn(conn)


def create_ingestion_job(
    job_id: str,
    file_name: str,
    content_hash: str,
    comuna: str,
    rubros: list[str],
    vigencia_desde: str | None,
    vigencia_hasta: str | None,
    uploaded_by: str,
) -> None:
    """Crea el registro de auditoría/estado de una subida, con status='queued'.

    `job_id` (uuid, generado por el caller) se usa como `id` (PK) de la fila
    Y como `job_id` de arq (ver services/ingestion_jobs.py) — un solo
    identificador para toda la cadena: respuesta HTTP, polling del panel,
    fila en Postgres y job de arq, sin ambigüedad entre dos ids distintos."""
    import dependencies

    conn = dependencies.db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ingestion_jobs
                    (id, job_id, file_name, content_hash, comuna, rubros,
                     vigencia_desde, vigencia_hasta, uploaded_by, status)
                VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, 'queued')
                """,
                (
                    job_id,
                    job_id,
                    file_name,
                    content_hash,
                    comuna,
                    rubros,
                    vigencia_desde,
                    vigencia_hasta,
                    uploaded_by,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        dependencies.db_pool.putconn(conn)


def update_ingestion_job(job_id: str, **fields) -> None:
    """Actualiza campos de un ingestion_job existente (status, chunks_written,
    review_flag, review_details, error_message, etc.). `job_id` es el id
    (uuid) de la fila, no el job_id de arq."""
    import dependencies

    if not fields:
        return

    fields = dict(fields)
    if "review_details" in fields and fields["review_details"] is not None:
        fields["review_details"] = json.dumps(fields["review_details"], ensure_ascii=False)

    set_clauses = ", ".join(f"{key} = %s" for key in fields)
    values = list(fields.values()) + [job_id]

    conn = dependencies.db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE ingestion_jobs
                SET {set_clauses}, updated_at = now()
                WHERE id = %s::uuid
                """,
                values,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        dependencies.db_pool.putconn(conn)


def get_ingestion_job(job_id: str) -> dict | None:
    """Obtiene un ingestion_job por id (uuid). Devuelve None si no existe o
    si `job_id` no es un uuid válido."""
    import dependencies

    conn = dependencies.db_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute("SELECT * FROM ingestion_jobs WHERE id = %s::uuid", (job_id,))
            except Exception:
                conn.rollback()
                return None
            row = cur.fetchone()
        return dict(row) if row else None
    finally:
        dependencies.db_pool.putconn(conn)


def delete_ingested_document(file_name: str, job_id: str, deleted_by: str) -> int:
    """Elimina el contenido ya ingerido de un documento: borra sus filas de
    `documents` (el asistente deja de citarlo) y marca el `ingestion_jobs`
    correspondiente con deleted_at/deleted_by, sin borrar el registro de
    auditoría en sí. Devuelve cuántas filas se borraron de `documents`."""
    import dependencies

    conn = dependencies.db_pool.getconn()
    try:
        with conn.cursor() as cur:
            deleted_rows = delete_document_rows(cur, file_name)
            cur.execute(
                """
                UPDATE ingestion_jobs
                SET deleted_at = now(), deleted_by = %s, updated_at = now()
                WHERE id = %s::uuid
                """,
                (deleted_by, job_id),
            )
        conn.commit()
        return deleted_rows
    except Exception:
        conn.rollback()
        raise
    finally:
        dependencies.db_pool.putconn(conn)


def get_stale_ingestion_jobs(older_than_minutes: int = 60) -> list[dict]:
    """Jobs que quedaron atascados en queued/processing por más de
    older_than_minutes — indicio de que el worker murió a mitad de camino
    (kill forzado, OOM, cancelación por timeout de arq) sin llegar a
    actualizar el estado ni a limpiar su archivo temporal en Storage. Usado
    por worker.py::cleanup_orphaned_uploads_job."""
    import dependencies

    conn = dependencies.db_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM ingestion_jobs
                WHERE status IN ('queued', 'processing')
                  AND created_at < now() - (%s || ' minutes')::interval
                """,
                (older_than_minutes,),
            )
            rows = cur.fetchall()
        return [dict(row) for row in rows]
    finally:
        dependencies.db_pool.putconn(conn)


def list_recent_jobs(comuna: str, limit: int = 20) -> list[dict]:
    """Últimas subidas de una comuna, más recientes primero — para la tabla
    de historial en /admin/documentos."""
    import dependencies

    conn = dependencies.db_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM ingestion_jobs
                WHERE comuna = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (comuna, limit),
            )
            rows = cur.fetchall()
        return [dict(row) for row in rows]
    finally:
        dependencies.db_pool.putconn(conn)
