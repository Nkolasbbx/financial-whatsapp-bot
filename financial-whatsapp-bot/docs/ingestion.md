# Ingesta automática de documentos (HdU15)

Documentación general de la funcionalidad que permite a un admin municipal subir un PDF o Markdown desde el panel para que el asistente lo use automáticamente, sin intervención manual.

## 1. Resumen y motivación

**Historia de usuario:** como administrador, quiero cargar documentos PDF y Markdown desde el panel para que se almacenen automáticamente en la base de datos y el asistente entregue esa información actualizada.

**Criterios de aceptación:**
- La ingesta debe ser **asíncrona y no bloqueante**: subir el archivo no debe hacer esperar al admin a que termine el procesamiento, y la nueva información debe quedar consultable.
- Cada documento guardado debe llevar **comuna**, **rubros aplicables** y **fecha de vigencia**.

**Por qué no se reutilizó `Ingest/ingest_supabase_v2.py` tal cual:** ese script (parent-child + contextual retrieval, funcionando bien) tiene tres problemas para esta HdU:
1. Es un script de línea de comandos, bloqueante — no encaja con "asíncrono y no bloqueante".
2. Hace `TRUNCATE TABLE documents` en cada corrida (recarga total) — no permite subir un documento sin borrar los demás.
3. Depende de `torch` + `sentence-transformers` (embeddings locales) y Tesseract (OCR) — dependencias pesadas que encarecen y complican el build en Railway.

Se portó su lógica de chunking (que sí es sólida) a un módulo nuevo, adaptándola para correr dentro de la infraestructura que ya existe en Railway sin esas dependencias pesadas.

## 2. Arquitectura elegida

**"Todo-en-uno, sin OCR, sin servicio Railway nuevo."** Se evaluaron 3 opciones con el usuario antes de implementar:

| Opción | Costo/infra | Elegida |
|---|---|---|
| Todo-en-uno (reusar server web + worker arq existentes) | Cero servicios nuevos, solo llamadas a la API de HF | ✅ |
| Todo-en-uno + OCR desde el día 1 | Igual, pero build más pesado y procesamiento más lento | ❌ (no había evidencia de necesitarlo) |
| Microservicio de ingesta separado (torch/sentence-transformers local) | Servicio Railway nuevo, imagen ~1GB+, más RAM/CPU | ❌ (más caro sin necesidad) |

Decisiones concretas:
- **Sin servicio Railway nuevo**: se reutiliza el server web (FastAPI) y el worker (`arq` + Redis) que ya corren 24/7 para el bot de WhatsApp.
- **Embeddings vía API remota de Hugging Face** (`intfloat/multilingual-e5-base`), el mismo modelo que ya usa el bot para las consultas (`core/ia.py`) — se evita cargar `torch`/`sentence-transformers` en producción.
- **Sin OCR**: se verificó con `pdftotext`/`pdfimages` que los PDFs del proyecto son 100% digitales (texto real, no escaneos). Como red de seguridad barata, se agregó una heurística (`detect_low_text_pages`) que marca páginas con mucha imagen y poco texto para revisión manual, sin bloquear la ingesta. Si en el futuro se necesita OCR real (ej. ordenanzas escaneadas), se puede agregar `pymupdf4llm(use_ocr=True)` sin rediseñar nada.
- **Ingesta incremental**: se reemplazó el `TRUNCATE` por `DELETE ... WHERE metadata->>'file_name' = ...` + `INSERT`, dentro de una sola transacción — subir o re-subir un documento no afecta a los demás.
- **Contextual retrieval (frase de contexto por chunk generada con un LLM) queda apagado por defecto** (`INGESTION_ENABLE_CONTEXTUAL=false`) — agrega latencia/costo/un punto de falla externo sin ser necesario para cumplir la HdU. El código está portado y listo (reutiliza el cliente Groq que ya existe vía `RES_URL`/`RES_MODEL`/`RES_KEY`), se activa con un solo env var.

## 3. Flujo

**Subida y procesamiento:**
```
Admin (navegador)
  → POST /admin/documentos/subir   (routers/admin.py)
      - valida tipo/tamaño
      - comuna = la de la sesión logueada (nunca del formulario)
      - sube el archivo a Supabase Storage (bucket "ingestion-uploads")
      - crea una fila en ingestion_jobs (status=queued)
      - encola process_document_ingestion_task en arq
      - responde 202 inmediatamente  ←── acá se cumple "no bloqueante"
  ...
  Worker (arq, proceso aparte)
      - descarga el archivo de Storage
      - si es PDF: pymupdf4llm.to_markdown() (sin OCR) + detect_low_text_pages()
      - si es .md: se usa el texto tal cual
      - build_parent_child_chunks() (chunking parent/child)
      - embed_batch_remoto() (HF Inference API, en lotes)
      - upsert_document() → DELETE + INSERT incremental en `documents`
      - actualiza ingestion_jobs (status=done/failed, chunks_written, review_flag)
      - borra el archivo temporal de Storage (éxito o error)
```

**Cómo el asistente usa lo ingerido** (sin cambios de código adicionales — ya lee de la misma tabla):
```
Usuario de WhatsApp pregunta algo
  → core/ia.py::obtener_contexto_rag()
      - genera el embedding de la pregunta (HF Inference API)
      - SELECT ... FROM documents WHERE comuna coincide
          AND (vigencia_hasta vencida => se excluye)
        ORDER BY distancia coseno LIMIT 4
      - se inyecta como contexto en el prompt del LLM
```

**Eliminar un documento ya ingerido:**
```
Admin → POST /admin/documentos/{job_id}/eliminar
      - valida que el job pertenezca a su comuna y esté en status=done
      - DELETE FROM documents WHERE file_name = ...
      - marca ingestion_jobs.deleted_at/deleted_by (no borra el registro de auditoría)
```

**Limpieza de huérfanos (cron horario):** ver sección 7.

## 4. Archivos

| Archivo | Rol |
|---|---|
| `core/ingestion.py` | Pipeline de chunking parent-child (portado de `Ingest/ingest_supabase_v2.py`), extracción de PDF sin OCR, heurística de contenido no legible, embeddings remotos en batch, contextual retrieval opcional. |
| `db/documents.py` | Acceso a Postgres crudo (psycopg2, vía `dependencies.db_pool`) para `documents` e `ingestion_jobs`: upsert incremental, CRUD de jobs, borrado de documento, detección de jobs huérfanos. |
| `services/ingestion_jobs.py` | Orquesta la subida: valida el archivo, sube a Supabase Storage, crea el job, encola en arq. |
| `routers/admin.py` | Endpoints del panel: `GET/POST /admin/documentos` (formulario + historial), `GET /admin/documentos/estado/{job_id}` (polling), `POST /admin/documentos/{job_id}/eliminar`. |
| `worker.py` | `process_document_ingestion_task` (job de arq que hace todo el procesamiento pesado) y `cleanup_orphaned_uploads_job` (cron de limpieza). |
| `core/ia.py` | `obtener_embeddings_remotos_batch` (generalizado para aceptar lotes, reutilizado por la ingesta) y el filtro de vigencia agregado a la query RAG. |
| `dependencies.py` | Crea el bucket `ingestion-uploads` de forma idempotente al iniciar. |
| `config.py` | Variables `INGESTION_*` (ver sección 6). |
| `supabase/migrations/20260916_document_ingestion.sql` | Índice funcional en `documents` + tabla `ingestion_jobs`. |
| `supabase/migrations/20260918_document_lifecycle.sql` | Columnas `deleted_at`/`deleted_by` en `ingestion_jobs` (feature de eliminar documento). |
| `static/admin.css` | Estilos del formulario de subida, badges de estado, botón de eliminar. |
| `tests/test_ingestion.py`, `tests/test_ingestion_worker.py`, `tests/test_db_documents.py` | Tests unitarios (chunking, heurística, embeddings en batch, job de arq, cron de limpieza, borrado). |

## 5. Esquema de datos

### `documents.metadata` (jsonb) — una fila por chunk "child"

```json
{
  "file_name": "ordenanza-patentes.pdf",
  "file_type": "pdf",
  "source": "Panel admin — InnovaRecoleta",
  "source_url": "",
  "source_date": "2026-09-17",
  "comuna": "Recoleta",
  "rubros": ["general"],
  "vigencia_desde": null,
  "vigencia_hasta": "2027-12-31",
  "content_hash": "3a7f...",
  "uploaded_by": "InnovaRecoleta",
  "uploaded_at": "2026-09-17T15:30:00+00:00",
  "review_flag": false,
  "parent_id": "a1b2c3d4e5f6a1b2",
  "child_index": 3,
  "section_header": "# Artículo 12",
  "context_summary": "",
  "child_text": "El monto de la patente..."
}
```
`content` (columna aparte, no jsonb) guarda el texto completo del **parent** (contexto amplio); `embedding` es el vector de 768 dimensiones del **child** (+ `context_summary` si `INGESTION_ENABLE_CONTEXTUAL=true`).

### `ingestion_jobs` — una fila por subida

| Columna | Ejemplo |
|---|---|
| `id` / `job_id` | mismo uuid, usado como id de arq y de la fila |
| `file_name` | `ordenanza-patentes.pdf` |
| `content_hash` | sha256 del archivo original |
| `comuna` | `Recoleta` |
| `rubros` | `{general}` |
| `vigencia_desde` / `vigencia_hasta` | `null` / `2027-12-31` |
| `uploaded_by` | `InnovaRecoleta` |
| `status` | `queued` \| `processing` \| `done` \| `failed` |
| `chunks_written` | `42` |
| `review_flag` / `review_details` | `true` / `{"flagged_pages": [{"page": 7, ...}]}` |
| `error_message` | `null` si no falló |
| `deleted_at` / `deleted_by` | `null` hasta que un admin lo elimina |

## 6. Variables de entorno

Nuevas para esta funcionalidad (todas con default razonable, no es obligatorio setearlas):

| Variable | Default | Para qué |
|---|---|---|
| `INGESTION_MAX_UPLOAD_MB` | `20` | Tamaño máximo de archivo aceptado por el endpoint. Debe quedar por debajo del límite de la plataforma (50MB en el free tier de Supabase Storage, ver sección 7). |
| `INGESTION_STORAGE_BUCKET` | `ingestion-uploads` | Bucket de Supabase Storage usado como almacenamiento temporal mientras se procesa. |
| `INGESTION_EMBEDDING_BATCH_SIZE` | `16` | Cuántos textos se envían por request a la API de embeddings de Hugging Face. |
| `INGESTION_JOB_TIMEOUT_SECONDS` | `300` | Timeout del job de arq (también aplica a `process_ai_task`, que normalmente responde en segundos). |
| `INGESTION_ENABLE_CONTEXTUAL` | `false` | Si es `true`, genera una frase de contexto por chunk con el LLM de Groq (`RES_URL`/`RES_MODEL`/`RES_KEY`) antes de embeberlo — mejora la calidad del retrieval a costa de latencia/costo extra. |

No se necesita ninguna API key nueva: se reutilizan `HF_TOKEN`/`EMBEDDING_MODEL_NAME` (embeddings) y `RES_URL`/`RES_MODEL`/`RES_KEY` (Groq, solo si se activa contextual retrieval).

Ejemplo de bloque en `.env` (valores ficticios — nunca pegar credenciales reales en un doc del repo):
```bash
# HdU15 — Ingesta automática de documentos
INGESTION_MAX_UPLOAD_MB=20
INGESTION_STORAGE_BUCKET=ingestion-uploads
INGESTION_EMBEDDING_BATCH_SIZE=16
INGESTION_JOB_TIMEOUT_SECONDS=300
INGESTION_ENABLE_CONTEXTUAL=false

# Ya existentes, reutilizadas por esta funcionalidad:
SUPABASE_URL=https://tu-proyecto.supabase.co
SUPABASE_SERVICE_ROLE_KEY=tu_service_role_key
DB_DSN=postgresql://usuario:password@host:5432/postgres?sslmode=require
HF_TOKEN=hf_tu_token
EMBEDDING_MODEL_NAME=intfloat/multilingual-e5-base
RES_URL=https://api.groq.com/openai/v1
RES_MODEL=groq/compound-mini
RES_KEY=gsk_tu_key
ADMIN_RECOLETA_PASSWORD=una_contraseña
ADMIN_ELBOSQUE_PASSWORD=otra_contraseña
```

Si se agrega o cambia alguna de estas en Railway, hay que replicarla en `.railway/railway.ts` en **ambos** servicios (`financialWhatsappBot` y `incredibleAdventure`, el worker) — ver `docs/railway-deploy.md`.

## 7. Límites conocidos y mitigaciones

**Supabase Storage, plan Free** (verificado en [supabase.com/pricing](https://supabase.com/pricing), septiembre 2026):
- **50 MB por archivo** — límite duro de la plataforma. `INGESTION_MAX_UPLOAD_MB` (default 20MB) ya queda cómodamente debajo; si se sube ese valor por encima de 50MB, Supabase rechazará el upload igual, independientemente de nuestra propia validación.
- **1 GB de almacenamiento total incluido.**

El diseño minimiza el uso del total: el archivo original solo vive en Storage mientras el worker lo procesa (segundos a un par de minutos) y se borra automáticamente al terminar, con éxito o con error (`finally` en `process_document_ingestion_task`). En operación normal el bucket queda casi vacío.

**Riesgo residual:** si el proceso del worker muere de forma abrupta a mitad de un job (kill forzado, OOM, un timeout de arq que cancela la tarea antes de que el bloque `except`/`finally` termine de correr), el archivo puede quedar huérfano en Storage y el `ingestion_jobs` correspondiente queda atascado en `processing` para siempre.

**Mitigación:** `worker.py::cleanup_orphaned_uploads_job`, un cron de arq (mismo patrón que `run_reminders_job`, corre cada hora a la media hora) que busca jobs en `queued`/`processing` con más de 60 minutos de antigüedad, intenta borrar su archivo de Storage (reconstruyendo la ruta desde `content_hash`+`file_name`, sin necesidad de listar el bucket) y los marca `failed` con un mensaje claro. Esto además soluciona un problema de UX: sin esto, un job que queda a medias se ve "Procesando" para siempre en el panel.

## 8. Cómo probarlo localmente

1. Variables necesarias en `.env`: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `DB_DSN`, `HF_TOKEN`, `REDIS_URL`, `ADMIN_RECOLETA_PASSWORD`/`ADMIN_ELBOSQUE_PASSWORD`. Si el `.env` apunta a la misma base que producción, poner `REMINDERS_ENABLED=false` y `CALENDAR_REMINDERS_ENABLED=false` para no disparar WhatsApps reales mientras el worker corre localmente.
2. Aplicar las migraciones: `psql "$DB_DSN" -f supabase/migrations/20260916_document_ingestion.sql` y `-f supabase/migrations/20260918_document_lifecycle.sql`.
3. Dos terminales: `uvicorn main:app --reload` (server) y `arq worker.WorkerSettings` (worker — sin esto los jobs se quedan en `queued`).
4. `http://localhost:8000/admin/login` (usuarios `recoleta`/`elbosque`, contraseñas en `.env`) → `/admin/documentos`.
5. Subir un archivo, esperar `status: done`, verificar en `documents` que aparecieron filas nuevas, y probar `/test/chat` con una pregunta relacionada.
6. Probar "Eliminar" en la tabla de historial y confirmar que las filas de `documents` desaparecen.

## 9. Extensiones futuras (mencionadas, no implementadas)

- **OCR real** para PDFs escaneados, si algún día las municipalidades suben documentos de ese tipo (hoy no hay evidencia de que se necesite — ver sección 2).
- **Filtrar/priorizar por rubro del usuario** en la query RAG de `core/ia.py` — hoy `rubros` se guarda en cada documento pero no se usa como filtro; requiere antes normalizar el campo `rubro` libre de `users` contra el mismo vocabulario cerrado de `core.onboarding.RUBROS_ACTIVOS`.
- **Contextual retrieval activado por defecto** (`INGESTION_ENABLE_CONTEXTUAL=true`) si se decide que la mejora de calidad justifica la latencia/costo extra.

## 10. Comportamiento conocido a tener en cuenta

Si se sube un archivo vacío o sin contenido extraíble (0 bytes, o solo espacios en blanco), el job termina en `status: done` con `chunks_written: 0` — no falla, pero tampoco agrega nada al índice. No hay hoy un aviso explícito en el panel para este caso puntual (se distingue de un error real: no hay `error_message`). Si se vuelve un problema recurrente, la mejora sería marcar ese caso como `failed` con un mensaje ("el documento no tiene contenido extraíble") en vez de `done`.
