-- HdU15 (parte 2): permite al admin eliminar un documento ya ingerido.
-- deleted_at/deleted_by son columnas nuevas en lugar de reutilizar `status`:
-- status describe el ciclo de vida del PROCESAMIENTO (queued/processing/
-- done/failed), mientras que "eliminado" describe si el contenido sigue
-- vigente en el índice de `documents` — son dos dimensiones distintas.
alter table public.ingestion_jobs
    add column if not exists deleted_at timestamptz,
    add column if not exists deleted_by text;
