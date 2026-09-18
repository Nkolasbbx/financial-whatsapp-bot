"""
FinancIAl — core/ingestion.py

Pipeline de ingesta de documentos (HdU15): chunking parent-child +
"contextual retrieval" opcional + embeddings remotos, portado desde el
script standalone Ingest/ingest_supabase_v2.py para poder correr dentro del
worker de arq en Railway sin las dependencias pesadas de ese script
(sentence-transformers/torch para embeddings locales, Tesseract para OCR).

Diferencias respecto al script original:
- Embeddings: vía la API remota de Hugging Face (embed_batch_remoto),
  reutilizando el mismo modelo que ya usa el bot para las consultas
  (core.ia.obtener_embeddings_remotos_batch), en vez de sentence-transformers
  local.
- Sin OCR: extract_pdf_to_markdown asume PDFs digitales (confirmado con el
  corpus actual del proyecto). En su lugar, detect_low_text_pages marca
  páginas sospechosas (mucha imagen, poco texto) para revisión manual, sin
  bloquear la ingesta.
- Sin infer_metadata() por heurística de nombre de archivo: la metadata
  (comuna, rubros, vigencia, etc.) viene siempre explícita desde el panel
  admin, vía el parámetro extra_meta de process_document_to_rows.
- generate_parent_context_map usa core.ia.llamar_llm (Groq, RES_URL/MODEL/KEY
  ya configurados) en vez de un cliente OpenAI apuntando a un LLM aparte.
"""
import asyncio
import hashlib
import json
import logging
import re
import time

import httpx
import pymupdf4llm

from config import INGESTION_EMBEDDING_BATCH_SIZE, INGESTION_ENABLE_CONTEXTUAL, RES_KEY, RES_MODEL, RES_URL
from core.ia import llamar_llm, obtener_embeddings_remotos_batch

logger = logging.getLogger("financial")

CHUNK_SIZE = 400          # tamaño del chunk "child" (unidad que se embebe y se busca)
CHUNK_OVERLAP = 150
MIN_CHUNK_SIZE = 100
PARENT_CHUNK_SIZE = 2800  # tamaño del chunk "parent" (contexto que se entrega al LLM)

CONTEXT_BATCH_SIZE = 2      # children por llamada LLM (ver nota en generate_parent_context_map)
CONTEXT_MAX_RETRIES = 3

# Heurística de "posible contenido no legible" (sin OCR): una página se marca
# para revisión manual si tiene poco texto extraído Y una porción grande de
# su área cubierta por imágenes incrustadas (posible tabla/texto pegado como
# captura de pantalla).
LOW_TEXT_THRESHOLD_CHARS = 200
HIGH_IMAGE_AREA_RATIO = 0.5


# =====================================================================
# CHUNKING SEMÁNTICO PARENT-CHILD (idéntico a Ingest/ingest_supabase_v2.py)
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

    1) El texto se agrupa por encabezado (##) en secciones.
    2) Cada sección se trocea en una o más ventanas "parent" (~parent_size
       caracteres): el bloque de contexto que se guarda como `content` y se
       le entrega al LLM en la respuesta final.
    3) Cada parent se subdivide en chunks "child" (~child_size caracteres,
       más pequeños y precisos): la unidad que efectivamente se embebe y
       contra la que se hace la búsqueda vectorial.

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


def extract_markdown_frontmatter(text: str) -> tuple[dict, str]:
    """Separa el frontmatter YAML-ish (--- ... ---) de un .md, si existe."""
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
# EXTRACCIÓN DE PDF (sin OCR) + heurística de contenido no legible
# =====================================================================
def detect_low_text_pages(
    pdf_path: str,
    text_threshold: int = LOW_TEXT_THRESHOLD_CHARS,
    image_area_ratio_threshold: float = HIGH_IMAGE_AREA_RATIO,
) -> list[dict]:
    """Heurística barata para detectar páginas donde el contenido relevante
    podría estar "atrapado" en una imagen (ej. una tabla pegada como captura
    de pantalla) en vez de en texto extraíble.

    No bloquea la ingesta: el resultado se guarda en
    ingestion_jobs.review_details para que un admin lo revise manualmente.
    """
    import pymupdf  # ya viene instalado como dependencia de pymupdf4llm

    flagged = []
    doc = pymupdf.open(pdf_path)
    try:
        for i, page in enumerate(doc):
            text_len = len(page.get_text("text").strip())
            page_area = page.rect.width * page.rect.height
            image_area = 0.0
            for img in page.get_images(full=True):
                try:
                    bbox = page.get_image_bbox(img)
                    image_area += bbox.width * bbox.height
                except Exception:
                    continue
            ratio = (image_area / page_area) if page_area else 0.0
            if ratio > image_area_ratio_threshold and text_len < text_threshold:
                flagged.append({
                    "page": i + 1,
                    "text_chars": text_len,
                    "image_area_ratio": round(ratio, 2),
                })
    finally:
        doc.close()
    return flagged


def extract_pdf_to_markdown(pdf_path: str) -> tuple[str, list[dict]]:
    """Convierte un PDF a Markdown (sin OCR: los PDFs esperados son digitales)
    y detecta páginas con posible contenido no legible."""
    markdown_text = pymupdf4llm.to_markdown(pdf_path, use_ocr=False)
    flagged_pages = detect_low_text_pages(pdf_path)
    return markdown_text, flagged_pages


# =====================================================================
# CONTEXTUAL RETRIEVAL (LLM, opcional — INGESTION_ENABLE_CONTEXTUAL)
# =====================================================================
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
    """Genera, en una llamada LLM por lote de children de un mismo parent,
    una frase de contexto por child (técnica "Contextual Retrieval" de
    Anthropic). Usa el mismo cliente Groq (RES_URL/RES_MODEL/RES_KEY) que ya
    usa el bot para resúmenes de conversación, vía core.ia.llamar_llm.

    Ante cualquier falla retorna {} y esos children se embeben sin contexto
    extra: nunca se pierde un chunk por esto. Solo corre si
    INGESTION_ENABLE_CONTEXTUAL=true (ver core.ia.actualizar_resumen_conversacion
    para el mismo patrón de cliente Groq).
    """
    if not INGESTION_ENABLE_CONTEXTUAL or not RES_KEY or not children:
        return {}

    numbered = "\n".join(f"[{pos}] {c['child_text']}" for pos, c in enumerate(children))
    prompt = CONTEXT_BATCH_PROMPT.format(doc_title=doc_title, parent=parent_text, numbered_children=numbered)
    max_tokens = min(300 + 120 * len(children), 4000)
    expected_positions = set(range(len(children)))

    delay = 2.0
    for attempt in range(CONTEXT_MAX_RETRIES + 1):
        raw = llamar_llm(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.0,
            ollama_url=RES_URL,
            ollama_model=RES_MODEL,
            ia_api_key=RES_KEY,
        )
        if not raw:
            if attempt >= CONTEXT_MAX_RETRIES:
                logger.warning("Contexto LLM: sin respuesta tras agotar reintentos")
                return {}
            time.sleep(delay)
            delay *= 2
            continue

        try:
            cleaned = _JSON_FENCE_RE.sub("", raw.strip()).strip()
            data = json.loads(cleaned)
            position_map = {int(k): str(v).strip() for k, v in data.items()}
        except (json.JSONDecodeError, ValueError, AttributeError):
            logger.warning("Contexto LLM: respuesta no es JSON válido, se omite")
            return {}

        missing = expected_positions - position_map.keys()
        if missing and attempt < CONTEXT_MAX_RETRIES:
            time.sleep(delay)
            delay *= 2
            continue
        if missing:
            logger.warning(
                "Contexto LLM: %d/%d fragmentos sin contexto tras agotar reintentos",
                len(missing),
                len(children),
            )
        return {
            children[pos]["child_index"]: text
            for pos, text in position_map.items()
            if 0 <= pos < len(children)
        }

    return {}


# =====================================================================
# ARMADO DE FILAS LISTAS PARA INSERTAR
# =====================================================================
def process_document_to_rows(file_name: str, text: str, file_type: str, extra_meta: dict) -> list[dict]:
    """Convierte el texto ya extraído de un documento en filas Parent-Child
    listas para embeber e insertar.

    A diferencia del script standalone, extra_meta es obligatorio y ya trae
    toda la metadata de negocio (comuna, rubros, vigencia, content_hash,
    uploaded_by, uploaded_at, source, review_flag) resuelta por el endpoint
    del panel admin — no hay heurística de inferencia por nombre de archivo.

    Cada fila resultante: {"content": <parent completo>, "embed_text":
    <contexto + child, lo que se embebe>, "meta": {...}}.
    """
    meta = dict(extra_meta)
    meta["file_name"] = file_name
    meta["file_type"] = file_type
    doc_title = meta.get("source") or file_name

    items = build_parent_child_chunks(text)
    if not items:
        return []

    parents: dict[str, dict] = {}
    for item in items:
        group = parents.setdefault(item["parent_id"], {
            "parent_text": item["parent_text"], "header": item["header"], "children": [],
        })
        group["children"].append(item)

    def _context_map_for_children(parent_text, children):
        context_map = {}
        for i in range(0, len(children), CONTEXT_BATCH_SIZE):
            batch = children[i:i + CONTEXT_BATCH_SIZE]
            context_map.update(generate_parent_context_map(doc_title, parent_text, batch))
        return context_map

    all_rows = []
    for parent_id, group in parents.items():
        context_map = (
            _context_map_for_children(group["parent_text"], group["children"])
            if INGESTION_ENABLE_CONTEXTUAL
            else {}
        )
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
            all_rows.append({"content": item["parent_text"], "embed_text": embed_text, "meta": row_meta})

    return all_rows


# =====================================================================
# EMBEDDINGS REMOTOS EN BATCH
# =====================================================================
async def _embed_batch_con_reintentos(
    lote: list[str], prefix: str, max_retries: int = 3
) -> list[list[float]]:
    delay = 2.0
    for attempt in range(max_retries + 1):
        try:
            return await obtener_embeddings_remotos_batch(lote, prefix=prefix, timeout=60.0)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (429, 503) or attempt >= max_retries:
                raise
            logger.warning(
                "HF Inference API respondió %s, reintentando en %.0fs...",
                exc.response.status_code,
                delay,
            )
            await asyncio.sleep(delay)
            delay *= 2
    raise RuntimeError("No se pudo generar embeddings tras reintentos")


async def embed_batch_remoto(textos: list[str], prefix: str = "passage") -> list[list[float]]:
    """Genera embeddings para una lista de textos en lotes de
    INGESTION_EMBEDDING_BATCH_SIZE, para no exceder límites de la API
    remota de Hugging Face en documentos con muchos chunks."""
    resultados: list[list[float]] = []
    for i in range(0, len(textos), INGESTION_EMBEDDING_BATCH_SIZE):
        lote = textos[i:i + INGESTION_EMBEDDING_BATCH_SIZE]
        resultados.extend(await _embed_batch_con_reintentos(lote, prefix))
    return resultados
