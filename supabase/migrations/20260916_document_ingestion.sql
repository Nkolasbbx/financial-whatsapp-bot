-- HdU15: ingesta automática de documentos (panel admin municipal).
-- La tabla `documents` ya existe en producción, creada originalmente por
-- Ingest/ingest_supabase_v2.py::setup_db. El CREATE TABLE IF NOT EXISTS de
-- acá es solo defensivo/documentación: nunca se ejecuta TRUNCATE desde esta
-- migración ni desde el pipeline de ingesta del panel (ver core/ingestion.py),
-- que ahora es incremental (delete+insert por documento, no recarga total).
create extension if not exists vector;
create extension if not exists pgcrypto;

create table if not exists public.documents (
    id bigserial primary key,
    content text not null,
    metadata jsonb,
    embedding vector(768)
);

create index if not exists documents_embedding_hnsw_idx
    on public.documents using hnsw (embedding vector_cosine_ops);

-- Permite borrar/reemplazar solo las filas de un documento concreto (por
-- nombre de archivo) sin escanear toda la tabla, para soportar re-subidas
-- incrementales desde el panel.
create index if not exists documents_file_name_idx
    on public.documents ((metadata ->> 'file_name'));

-- Registro de auditoría/estado de cada subida hecha desde el panel admin.
-- Es la fuente de verdad que consulta el panel para mostrar
-- "procesando/listo/error" y el aviso de "posible contenido no legible"
-- (páginas con mucha imagen y poco texto, ver core/ingestion.py::detect_low_text_pages).
create table if not exists public.ingestion_jobs (
    id uuid primary key default gen_random_uuid(),
    job_id text not null,
    file_name text not null,
    content_hash text not null,
    comuna text not null,
    rubros text[] not null default '{}',
    vigencia_desde date,
    vigencia_hasta date,
    uploaded_by text not null,
    status text not null default 'queued',
    chunks_written integer,
    review_flag boolean not null default false,
    review_details jsonb,
    error_message text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint ingestion_jobs_status_check
        check (status in ('queued', 'processing', 'done', 'failed'))
);

create index if not exists ingestion_jobs_comuna_idx
    on public.ingestion_jobs (comuna, created_at desc);

create index if not exists ingestion_jobs_content_hash_idx
    on public.ingestion_jobs (content_hash);
