import os
import re
import json
import time
import hashlib
import psycopg2
from psycopg2.extras import execute_values
import torch
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
import pymupdf4llm
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

# =====================================================================
# CONFIGURACIÓN
# =====================================================================
load_dotenv()
DATA_DIR = "./data"
DOCS_DIR = "./docs"
METADATA_FILE = os.path.join(DATA_DIR, "message.txt")
TABLE_NAME = "documents"
DSN = os.getenv("SUPABASE_DSN")
MODELO = "intfloat/multilingual-e5-base"

CHUNK_SIZE = 400          # tamaño del chunk "child" (unidad que se embebe y se busca)
CHUNK_OVERLAP = 150
MIN_CHUNK_SIZE = 100
PARENT_CHUNK_SIZE = 2800  # tamaño del chunk "parent" (contexto que se entrega al LLM)

# --- Contextual Retrieval (LLM) ---
# Genera, para cada child chunk, una frase que lo sitúa dentro de su sección
# (técnica de Anthropic "Contextual Retrieval"). Reutiliza el mismo endpoint
# Groq (API compatible con OpenAI) que ya usa el bot para generar respuestas.
CONTEXT_LLM_URL = os.getenv("CONTEXT_LLM_URL", "")
CONTEXT_LLM_MODEL = os.getenv("CONTEXT_LLM_MODEL", "")
CONTEXT_LLM_API_KEY = os.getenv("CONTEXT_LLM_API_KEY", "")
ENABLE_CONTEXTUAL = os.getenv("ENABLE_CONTEXTUAL", "true").strip().lower() not in ("false", "0", "no")
CONTEXT_MAX_WORKERS = int(os.getenv("CONTEXT_MAX_WORKERS", "4"))
CONTEXT_MAX_RETRIES = int(os.getenv("CONTEXT_MAX_RETRIES", "3"))  # backoff exponencial ante rate limit (429)
# Máximo de children por llamada LLM. Modelos chicos (ej. qwen2.5:7b local)
# pierden ítems del JSON en lotes grandes aunque se les reintente con más
# tokens; acotar el lote es lo que realmente lo arregla (probado: batch=4 dio
# ~86% de cobertura, batch=2 dio 100% en los mismos documentos). Modelos más
# grandes (gpt-oss-20b, etc.) pueden subir esto vía env si se quiere menos
# llamadas y ya tienen mejor cobertura con lotes más grandes.
CONTEXT_BATCH_SIZE = int(os.getenv("CONTEXT_BATCH_SIZE", "2"))
# Solo los modelos "razonadores" (ej. openai/gpt-oss-* en Groq) necesitan/aceptan
# este parámetro; modelos planos como qwen2.5 lo rechazan con 400. Vacío = no
# se envía. Ver generate_parent_context_map().
CONTEXT_REASONING_EFFORT = os.getenv("CONTEXT_REASONING_EFFORT", "").strip()

# PDFs de docs/ que ya cuentan con una transcripción .md curada a mano en data/
# (tablas, planos e imágenes complejas de OCR); se omiten del pipeline de PDF
# para no duplicar contenido de peor calidad en la base vectorial.
PDF_FILES_WITH_MD = {
    "ORDENANZA MUNICIPAL.pdf",
    "PATENTES COMERCIALES.pdf",
    "ordenanza-local-_do-8-enero-2005.pdf",
    "plano-1-uso-de-suelo.pdf",
}

# Idioma(s) para el fallback de OCR de pymupdf4llm (Tesseract). Todos los
# documentos de este proyecto están en español.
OCR_LANGUAGE = "spa+eng"
OCR_DPI = 300

# docs/extracted_media fue curada a mano (se filtraron las imágenes
# irrelevantes). Mientras esto sea False, la ingesta no escribe ni sobrescribe
# imágenes ahí: el texto (nativo + OCR) igual se extrae normalmente, solo se
# omite el volcado de imágenes a disco. Cambiar a True para volver a generarlas.
WRITE_IMAGES = False
EXTRACTED_MEDIA_DIR = "./docs/extracted_media"

# Parámetros de aceleración
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
EMBEDDING_BATCH_SIZE = 128 if DEVICE == "cuda" else 32

# =====================================================================
# CHUNKING SEMÁNTICO
# =====================================================================
def split_by_headers(text, header_pattern=r"^(#{1,3})\s+(.*)$"):
    lines = text.split("\n")
    sections = []
    current_header = ""
    current_lines = []

    for line in lines:
        match = re.match(header_pattern, line)
        if match:
            if current_lines:
                sections.append((current_header, "\n".join(current_lines).strip()))
            current_header = line.strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_header, "\n".join(current_lines).strip()))

    return [s for s in sections if s[1]]

_LIST_ITEM_RE = re.compile(r"^\s*([-*+]|\d+\.)\s+")

def split_paragraphs(text):
    blocks = re.split(r"\n\s*\n", text)
    units = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        list_lines = [ln for ln in lines if _LIST_ITEM_RE.match(ln)]
        if len(list_lines) >= max(2, len(lines) // 2):
            current_item = ""
            for ln in lines:
                if _LIST_ITEM_RE.match(ln):
                    if current_item:
                        units.append(current_item.strip())
                    current_item = ln
                else:
                    current_item += "\n" + ln
            if current_item:
                units.append(current_item.strip())
        else:
            units.append(block)

    return units

def _split_long_paragraph(paragraph, size, overlap):
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    chunks = []
    current = ""

    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= size:
            current = candidate
        else:
            if current:
                chunks.append(current)
            if overlap and current:
                tail = current[-overlap:]
                space_idx = tail.find(" ")
                if space_idx != -1:
                    tail = tail[space_idx + 1:]
                current = f"{tail} {sentence}".strip()
            else:
                current = sentence

    if current:
        chunks.append(current)

    return chunks

def _merge_small_chunks(chunks, min_chunk_size):
    merged = []
    buffer = ""

    for chunk in chunks:
        buffer = f"{buffer}\n\n{chunk}" if buffer else chunk
        if len(buffer) >= min_chunk_size:
            merged.append(buffer)
            buffer = ""

    if buffer:
        if merged:
            merged[-1] = f"{merged[-1]}\n\n{buffer}"
        else:
            merged.append(buffer)

    return merged

def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP, min_chunk_size=MIN_CHUNK_SIZE):
    chunks = []
    sections = split_by_headers(text)

    if not sections:
        sections = [("", text)]

    for header, section_text in sections:
        if len(section_text) <= size:
            chunks.append(section_text)
            continue

        paragraphs = split_paragraphs(section_text)
        current_chunk = ""

        for para in paragraphs:
            candidate = f"{current_chunk}\n\n{para}".strip() if current_chunk else para

            if len(candidate) <= size:
                current_chunk = candidate
            else:
                if current_chunk:
                    chunks.append(current_chunk)

                if len(para) > size:
                    chunks.extend(_split_long_paragraph(para, size, overlap))
                    current_chunk = ""
                else:
                    prefix = f"{header}\n\n" if header else ""
                    current_chunk = f"{prefix}{para}".strip()

        if current_chunk:
            chunks.append(current_chunk)

    return _merge_small_chunks(chunks, min_chunk_size)

def build_parent_child_chunks(
    text,
    parent_size=PARENT_CHUNK_SIZE,
    child_size=CHUNK_SIZE,
    overlap=CHUNK_OVERLAP,
    min_chunk_size=MIN_CHUNK_SIZE,
):
    """Chunking Parent-Child.

    1) El texto se agrupa por encabezado (##) en secciones, igual que antes.
    2) Cada sección se trocea en una o más ventanas "parent" (~PARENT_CHUNK_SIZE
       caracteres): es el bloque de contexto que se guarda como `content` y
       se le entrega al LLM en la respuesta final.
    3) Cada parent se subdivide en chunks "child" (~CHUNK_SIZE caracteres,
       más pequeños y precisos): son la unidad que efectivamente se embebe y
       contra la que se hace la búsqueda vectorial.

    Reutiliza `chunk_text` en las dos granularidades en vez de duplicar la
    lógica de troceo por párrafos/oraciones.

    Devuelve una lista de dicts: parent_id, header, parent_text, child_text,
    child_index.
    """
    sections = split_by_headers(text)
    if not sections:
        sections = [("", text)]

    results = []
    for header, section_text in sections:
        if len(section_text) <= parent_size:
            parent_windows = [section_text]
        else:
            parent_windows = chunk_text(
                section_text,
                size=parent_size,
                overlap=0,
                min_chunk_size=parent_size // 4,
            )

        for parent_text in parent_windows:
            parent_id = hashlib.sha1(parent_text.encode("utf-8")).hexdigest()[:16]
            children = chunk_text(
                parent_text, size=child_size, overlap=overlap, min_chunk_size=min_chunk_size
            )
            for child_index, child_text in enumerate(children):
                results.append({
                    "parent_id": parent_id,
                    "header": header,
                    "parent_text": parent_text,
                    "child_text": child_text,
                    "child_index": child_index,
                })

    return results

# =====================================================================
# METADATA Y PDF PARSING
# =====================================================================
def load_document_metadata(path: str) -> dict:
    """Carga el diccionario DOCUMENT_METADATA definido en data/message.txt.

    El archivo contiene código Python (un dict literal) a pesar de su
    extensión .txt, con la metadata "source"/"source_url"/"source_date" por
    nombre de archivo .md. Es un archivo propio del proyecto, no una entrada
    externa, por lo que ejecutarlo para obtener el dict es seguro.
    """
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    namespace: dict = {}
    exec(compile(source, path, "exec"), namespace)
    return namespace.get("DOCUMENT_METADATA", {})

DOCUMENT_METADATA = load_document_metadata(METADATA_FILE)

def infer_metadata(file_name: str, file_type: str, extra: dict = None) -> dict:
    meta = {
        "file_name": file_name,
        "file_type": file_type,
        "source": "Local ingestion",
        "source_url": "local_storage",
        "source_date": "2026-08-25"
    }

    fn_lower = file_name.lower()
    if "recoleta" in fn_lower:
        meta["comuna"] = "recoleta"
    elif "bosque" in fn_lower:
        meta["comuna"] = "el bosque"
    elif "quinta" in fn_lower:
        meta["comuna"] = "quinta normal"
    else:
        meta["comuna"] = "general"

    if "step1" in fn_lower:
        meta["etapa"] = "constitucion"
    elif "step2" in fn_lower:
        meta["etapa"] = "sii_actividades"
    elif "step3" in fn_lower:
        meta["etapa"] = "patentes_permisos"
    else:
        meta["etapa"] = "general"

    # Metadata curada por archivo, cargada desde data/message.txt. Tiene
    # prioridad sobre las heurísticas anteriores para todos los .md listados.
    if file_name in DOCUMENT_METADATA:
        meta.update(DOCUMENT_METADATA[file_name])

    if extra:
        meta.update(extra)
    return meta

def extract_markdown_frontmatter(text: str) -> tuple[dict, str]:
    lines = text.splitlines()
    meta = {}
    content = text
    if lines and lines[0].strip() == "---":
        try:
            end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
            for line in lines[1:end]:
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                k, sep, v = line.partition(":")
                if sep:
                    meta[k.strip()] = v.strip().strip('"\'')
            content = "\n".join(lines[end + 1:]).strip()
        except StopIteration:
            pass
    return meta, content

# =====================================================================
# CONTEXTUAL RETRIEVAL (LLM)
# =====================================================================
_context_client = None

def _get_context_client():
    global _context_client
    if _context_client is None:
        from openai import OpenAI
        _context_client = OpenAI(base_url=CONTEXT_LLM_URL, api_key=CONTEXT_LLM_API_KEY)
    return _context_client

CONTEXT_BATCH_PROMPT = """Documento: {doc_title}

Sección del documento (contexto amplio):
<seccion>
{parent}
</seccion>

Esa sección se dividió en los siguientes fragmentos:
{numbered_children}

Para CADA fragmento numerado, escribe 1 frase corta en español (máximo 30 \
palabras) que lo sitúe dentro del documento: de qué trata la sección y \
cualquier dato clave (nombre del trámite, artículo, monto, zona, comuna) \
necesario para entenderlo sin ver el resto del documento.

Responde ÚNICAMENTE con un objeto JSON plano, sin texto adicional ni bloques \
de código, con una entrada por número de fragmento, por ejemplo:
{{"0": "frase para el fragmento 0", "1": "frase para el fragmento 1"}}"""

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

def generate_parent_context_map(doc_title: str, parent_text: str, children: list) -> dict:
    """Genera, en UNA sola llamada LLM por lote de children de un mismo
    parent (técnica "Contextual Retrieval" de Anthropic, adaptada para no
    depender de un servicio con prompt caching: en vez de reenviar el parent
    completo en una llamada por cada child —lo que agota el rate limit por
    tokens/minuto muy rápido en la nube— se envía el parent una sola vez
    junto con el lote de children y se le pide al LLM un mapeo
    {indice: frase} en JSON.

    Los índices que se usan en el prompt/respuesta son POSICIONALES dentro
    del lote (0, 1, 2...), no `child_index` real: modelos chicos como
    qwen2.5:7b no respetan índices arbitrarios (ej. [4],[5],[6]) y renumeran
    su respuesta empezando siempre en 0, lo que rompía el mapeo. Acá se
    traduce de vuelta a `child_index` real antes de retornar.

    Devuelve {child_index: frase}; ante cualquier falla (incluso tras
    reintentos) retorna {} y esos children se embeben sin contexto extra:
    nunca se pierde un chunk por esto.
    """
    if not ENABLE_CONTEXTUAL or not CONTEXT_LLM_API_KEY or not children:
        return {}
    from openai import RateLimitError

    client = _get_context_client()
    numbered = "\n".join(f"[{pos}] {c['child_text']}" for pos, c in enumerate(children))
    prompt = CONTEXT_BATCH_PROMPT.format(doc_title=doc_title, parent=parent_text, numbered_children=numbered)
    # Generoso a propósito: no hay costo de nube que cuidar en un modelo local,
    # y un presupuesto corto es la causa típica de que el modelo corte el JSON
    # a mitad de camino y omita fragmentos del final del lote.
    max_tokens = min(300 + 120 * len(children), 4000)
    expected_positions = set(range(len(children)))

    extra_body = {}
    if CONTEXT_REASONING_EFFORT:
        # Necesario para modelos "razonadores" (ej. openai/gpt-oss-* en Groq):
        # sin esto gastan cientos de tokens ocultos de razonamiento por llamada
        # y truncan la respuesta real antes de emitirla (finish_reason "length"
        # con content vacío). Modelos planos (ej. qwen2.5 en Ollama) rechazan
        # este parámetro con 400, por eso solo se envía si está configurado.
        extra_body["reasoning_effort"] = CONTEXT_REASONING_EFFORT

    delay = 2.0
    for attempt in range(CONTEXT_MAX_RETRIES + 1):
        try:
            resp = client.chat.completions.create(
                model=CONTEXT_LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=max_tokens,
                extra_body=extra_body,
            )
            raw = (resp.choices[0].message.content or "").strip()
            raw = _JSON_FENCE_RE.sub("", raw).strip()
            data = json.loads(raw)
            position_map = {int(k): str(v).strip() for k, v in data.items()}

            missing = expected_positions - position_map.keys()
            if missing and attempt < CONTEXT_MAX_RETRIES:
                # El modelo devolvió un JSON válido pero incompleto (típico de
                # modelos locales más chicos con lotes grandes): reintentar
                # antes de conformarse con contexto parcial.
                time.sleep(delay)
                delay *= 2
                continue
            if missing:
                print(f"   ⚠️ Contexto LLM: {len(missing)}/{len(children)} fragmentos sin contexto tras agotar reintentos (JSON incompleto).")
            # Traducir posición dentro del lote -> child_index real.
            return {
                children[pos]["child_index"]: text
                for pos, text in position_map.items()
                if 0 <= pos < len(children)
            }
        except RateLimitError as e:
            if attempt >= CONTEXT_MAX_RETRIES:
                print(f"   ⚠️ Contexto LLM: rate limit persistente para una sección, se omite ({e})")
                return {}
            time.sleep(delay)
            delay *= 2
        except Exception as e:
            print(f"   ⚠️ Contexto LLM (batch) falló para una sección: {e}")
            return {}
    return {}

def process_document_to_rows(fn: str, text: str, file_type: str, extra_meta: dict = None) -> list:
    """Convierte el texto ya extraído de un documento en filas Parent-Child
    listas para insertar: arma los chunks, agrupa los children por parent y
    genera el contexto de cada grupo en llamadas LLM de a lo sumo
    CONTEXT_BATCH_SIZE children por vez (en paralelo entre parents, vía
    ThreadPoolExecutor por ser I/O-bound), y arma la metadata final de cada
    fila.

    Cada fila resultante: {"content": <parent completo>,
    "embed_text": <contexto + child, lo que realmente se embebe>, "meta": {...}}.
    """
    meta = infer_metadata(fn, file_type, extra_meta)
    doc_title = meta.get("source") or fn
    items = build_parent_child_chunks(text)
    if not items:
        return []

    parents = {}
    for item in items:
        group = parents.setdefault(item["parent_id"], {
            "parent_text": item["parent_text"], "header": item["header"], "children": [],
        })
        group["children"].append(item)

    def _context_map_for_children(parent_text, children):
        """Parte los children de un parent en lotes de CONTEXT_BATCH_SIZE antes
        de pedirle contexto al LLM. Modelos chicos (ej. qwen2.5:7b local)
        omiten ítems del JSON cuando el lote es grande, sin que reintentar con
        más tokens lo arregle; lotes acotados es lo que de verdad lo resuelve.
        """
        context_map = {}
        for i in range(0, len(children), CONTEXT_BATCH_SIZE):
            batch = children[i:i + CONTEXT_BATCH_SIZE]
            context_map.update(generate_parent_context_map(doc_title, parent_text, batch))
        return context_map

    def _rows_for_parent(parent_id, group):
        context_map = _context_map_for_children(group["parent_text"], group["children"])
        rows = []
        for item in group["children"]:
            context = context_map.get(item["child_index"], "")
            embed_text = f"{context}\n\n{item['child_text']}".strip() if context else item["child_text"]
            row_meta = dict(meta)
            row_meta.update({
                "parent_id": parent_id,
                "child_index": item["child_index"],
                "section_header": item["header"],
                "context_summary": context,
                "child_text": item["child_text"],
            })
            rows.append({"content": item["parent_text"], "embed_text": embed_text, "meta": row_meta})
        return rows

    all_rows = []
    if ENABLE_CONTEXTUAL and CONTEXT_LLM_API_KEY:
        with ThreadPoolExecutor(max_workers=CONTEXT_MAX_WORKERS) as executor:
            futures = [executor.submit(_rows_for_parent, pid, g) for pid, g in parents.items()]
            for fut in futures:
                all_rows.extend(fut.result())
    else:
        for pid, g in parents.items():
            all_rows.extend(_rows_for_parent(pid, g))

    return all_rows

def _worker_process_pdf(file_path: str) -> tuple[str, str]:
    """Función de extracción por proceso worker.

    Usa el motor de layout de pymupdf4llm con OCR (Tesseract) habilitado como
    respaldo automático para páginas escaneadas o sin capa de texto legible
    (planos, sellos, tablas rasterizadas). El OCR solo se aplica a las
    regiones sin texto extraíble; el texto nativo del PDF nunca se reemplaza.
    Requiere Tesseract instalado en el sistema (paquetes tesseract-ocr y
    tesseract-ocr-spa) con datos de idioma español; si no está disponible, se
    reintenta sin OCR en vez de fallar la ingesta completa.

    El volcado de imágenes a docs/extracted_media está controlado por
    WRITE_IMAGES: la extracción de texto (nativo + OCR) no depende de eso.
    """
    fn = os.path.basename(file_path)
    write_images_kwargs = {}
    if WRITE_IMAGES:
        os.makedirs(EXTRACTED_MEDIA_DIR, exist_ok=True)
        write_images_kwargs = {
            "write_images": True,
            "image_path": EXTRACTED_MEDIA_DIR,
            "image_format": "png",
        }
    try:
        md_text = pymupdf4llm.to_markdown(
            file_path,
            **write_images_kwargs,
            use_ocr=True,
            ocr_language=OCR_LANGUAGE,
            ocr_dpi=OCR_DPI,
        )
        return fn, md_text
    except Exception as e:
        print(f"   ⚠️ OCR falló en {fn} ({e}); reintentando sin OCR...")
        try:
            md_text = pymupdf4llm.to_markdown(
                file_path,
                **write_images_kwargs,
                use_ocr=False,
            )
            return fn, md_text
        except Exception as e2:
            print(f"   ⚠️ Error en {fn}: {e2}")
            return fn, ""

# =====================================================================
# BASE DE DATOS
# =====================================================================
def setup_db(conn):
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                id bigserial PRIMARY KEY,
                content text NOT NULL,
                metadata jsonb,
                embedding vector(768)
            );
        """)
        cur.execute(f"TRUNCATE TABLE {TABLE_NAME} RESTART IDENTITY;")
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS {TABLE_NAME}_embedding_hnsw_idx
            ON {TABLE_NAME} USING hnsw (embedding vector_cosine_ops);
        """)
        conn.commit()
    print("🧹 Base de datos inicializada y tabla vaciada.")

# =====================================================================
# MAIN
# =====================================================================
def main():
    if not DSN:
        print("❌ Error: SUPABASE_DSN no está definida.")
        return

    print("🔌 Conectando a Supabase...")
    try:
        conn = psycopg2.connect(DSN)
        setup_db(conn)
    except Exception as e:
        print(f"❌ Error de BD: {e}")
        return

    print(f"⚡ Dispositivo de aceleración detectado: {DEVICE.upper()}")
    if DEVICE == "cuda":
        print(f"🚀 GPU: {torch.cuda.get_device_name(0)}")

    print(f"📦 Cargando modelo '{MODELO}' en {DEVICE.upper()}...")
    model = SentenceTransformer(MODELO, device=DEVICE)

    all_rows = []
    context_status = "ON" if (ENABLE_CONTEXTUAL and CONTEXT_LLM_API_KEY) else "OFF"
    print(f"🧩 Chunking: parent≈{PARENT_CHUNK_SIZE} / child≈{CHUNK_SIZE} chars — Contextual LLM: {context_status}")

    # 1. Markdown (.md)
    if os.path.exists(DATA_DIR):
        md_files = [f for f in sorted(os.listdir(DATA_DIR)) if f.endswith(".md")]
        print(f"📄 Procesando {len(md_files)} archivos .md...")
        for fn in md_files:
            with open(os.path.join(DATA_DIR, fn), "r", encoding="utf-8") as f:
                raw_text = f.read()
            fm_meta, clean_text = extract_markdown_frontmatter(raw_text)
            rows = process_document_to_rows(fn, clean_text, "markdown", fm_meta)
            print(f"   📄 {fn} → {len(rows)} child chunks")
            all_rows.extend(rows)

    # 2. PDF en paralelo (CPU multiprocessing para la extracción de texto/OCR;
    # el chunking parent-child y el contexto LLM se hacen después, en el
    # proceso principal, sobre el texto ya extraído de cada PDF).
    if os.path.exists(DOCS_DIR):
        pdf_paths = [
            os.path.join(DOCS_DIR, f)
            for f in sorted(os.listdir(DOCS_DIR))
            if f.endswith(".pdf") and f not in PDF_FILES_WITH_MD
        ]
        skipped = sorted(PDF_FILES_WITH_MD & set(os.listdir(DOCS_DIR)))
        if skipped:
            print(f"⏭️  Omitiendo {len(skipped)} PDFs con transcripción .md curada en data/: {', '.join(skipped)}")
        print(f"📑 Procesando {len(pdf_paths)} PDFs en paralelo...")
        with ProcessPoolExecutor() as executor:
            results = list(executor.map(_worker_process_pdf, pdf_paths))

        for fn, md_text in results:
            if md_text:
                rows = process_document_to_rows(fn, md_text, "pdf")
                print(f"   🖼️ [PDF] {fn} → {len(rows)} child chunks")
                all_rows.extend(rows)

    if not all_rows:
        print("❌ No se generaron chunks.")
        conn.close()
        return

    print(f"\n🔢 Generando embeddings para {len(all_rows)} chunks en GPU (Batch Size: {EMBEDDING_BATCH_SIZE})...")
    # Se embebe el child (+ contexto si se generó); se guarda y se devuelve
    # siempre el parent completo, así el RAG obtiene contexto amplio sin
    # tener que cambiar la consulta de recuperación.
    contenidos = [r["content"] for r in all_rows]
    textos_prefijados = [f"passage: {r['embed_text']}" for r in all_rows]

    embeddings = model.encode(
        textos_prefijados,
        batch_size=EMBEDDING_BATCH_SIZE,
        device=DEVICE,
        show_progress_bar=True,
        normalize_embeddings=True
    )

    print("🚀 Insertando datos masivamente en Supabase...")
    try:
        batch_data = [
            (contenidos[i], json.dumps(all_rows[i]["meta"], ensure_ascii=False), embeddings[i].tolist())
            for i in range(len(all_rows))
        ]
        with conn.cursor() as cur:
            execute_values(
                cur,
                f"INSERT INTO {TABLE_NAME} (content, metadata, embedding) VALUES %s",
                batch_data,
                template="(%s, %s::jsonb, %s::vector)",
                page_size=1000
            )
            conn.commit()
        print(f"💾 Ingesta limpia completada: {len(batch_data)} chunks indexados.")
    except Exception as e:
        print(f"❌ Error al insertar: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    main()