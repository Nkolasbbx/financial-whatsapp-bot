import os
import re
import json
import psycopg2
from psycopg2.extras import execute_values
import torch
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
import pymupdf4llm
from concurrent.futures import ProcessPoolExecutor

# =====================================================================
# CONFIGURACIÓN
# =====================================================================
load_dotenv()
DATA_DIR = "./data"
DOCS_DIR = "./docs"
TABLE_NAME = "documents"
DSN = os.getenv("SUPABASE_DSN")
MODELO = "intfloat/multilingual-e5-base"

CHUNK_SIZE = 800
CHUNK_OVERLAP = 150
MIN_CHUNK_SIZE = 100

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

# =====================================================================
# METADATA Y PDF PARSING
# =====================================================================
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

def _worker_process_pdf(file_path: str) -> tuple[str, str]:
    """Función de extracción por proceso worker."""
    media_dir = "./docs/extracted_media"
    os.makedirs(media_dir, exist_ok=True)
    fn = os.path.basename(file_path)
    try:
        md_text = pymupdf4llm.to_markdown(
            file_path,
            write_images=True,
            image_path=media_dir,
            image_format="png"
        )
        return fn, md_text
    except Exception as e:
        print(f"   ⚠️ Error en {fn}: {e}")
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
        #setup_db(conn)
    except Exception as e:
        print(f"❌ Error de BD: {e}")
        return

    print(f"⚡ Dispositivo de aceleración detectado: {DEVICE.upper()}")
    if DEVICE == "cuda":
        print(f"🚀 GPU: {torch.cuda.get_device_name(0)}")

    print(f"📦 Cargando modelo '{MODELO}' en {DEVICE.upper()}...")
    model = SentenceTransformer(MODELO, device=DEVICE)

    all_rows = []

    # 1. Markdown (.md)
    if os.path.exists(DATA_DIR):
        md_files = [f for f in sorted(os.listdir(DATA_DIR)) if f.endswith(".md")]
        print(f"📄 Procesando {len(md_files)} archivos .md...")
        for fn in md_files:
            with open(os.path.join(DATA_DIR, fn), "r", encoding="utf-8") as f:
                raw_text = f.read()
            fm_meta, clean_text = extract_markdown_frontmatter(raw_text)
            meta = infer_metadata(fn, "markdown", fm_meta)
            chunks = chunk_text(clean_text)
            for ch in chunks:
                all_rows.append((ch, meta))

    # 2. PDF en paralelo (CPU multiprocessing)
    """"
    if os.path.exists(DOCS_DIR):
        pdf_paths = [
            os.path.join(DOCS_DIR, f)
            for f in sorted(os.listdir(DOCS_DIR))
            if f.endswith(".pdf")
        ]
        print(f"📑 Procesando {len(pdf_paths)} PDFs en paralelo...")
        with ProcessPoolExecutor() as executor:
            results = list(executor.map(_worker_process_pdf, pdf_paths))

        for fn, md_text in results:
            if md_text:
                meta = infer_metadata(fn, "pdf")
                chunks = chunk_text(md_text)
                print(f"   🖼️ [PDF] {fn} → {len(chunks)} chunks")
                for ch in chunks:
                    all_rows.append((ch, meta))
    """
    if not all_rows:
        print("❌ No se generaron chunks.")
        conn.close()
        return

    print(f"\n🔢 Generando embeddings para {len(all_rows)} chunks en GPU (Batch Size: {EMBEDDING_BATCH_SIZE})...")
    textos = [r[0] for r in all_rows]
    textos_prefijados = [f"passage: {t}" for t in textos]
    
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
            (textos[i], json.dumps(all_rows[i][1], ensure_ascii=False), embeddings[i].tolist())
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