import os
import re
import json
import psycopg2
from psycopg2.extras import execute_values
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

# =====================================================================
# CONFIGURACIÓN
# =====================================================================
load_dotenv()
DATA_DIR = "./data"
TABLE_NAME = "documents"
# La contraseña vive en una variable de entorno, nunca en el código fuente.
# Antes de correr el script: export SUPABASE_DSN="postgresql://..."
DSN = os.environ["SUPABASE_DSN"]
MODELO = "intfloat/multilingual-e5-base"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 150
MIN_CHUNK_SIZE = 100

# Catalogo versionado de fuentes. `source_date` es la fecha de revision del
# contenido cargado; debe actualizarse cuando se confirme una nueva version.
DOCUMENT_METADATA = {
    "step1_constitution_general.md": {
        "source": "Ministerio de Economia, Fomento y Turismo; base de conocimiento del proyecto",
        "source_url": "https://www.economia.gob.cl/",
        "source_date": "2026-08-18",
    },
    "step2_sii_actividades_general.md": {
        "source": "Servicio de Impuestos Internos; base de conocimiento del proyecto",
        "source_url": "https://www.sii.cl/",
        "source_date": "2026-08-18",
    },
    "step3_permisos_el_bosque.md": {
        "source": "Municipalidad de El Bosque; base de conocimiento del proyecto",
        "source_url": "https://www.municipalidadelbosque.cl/",
        "source_date": "2026-08-18",
    },
    "step3_permisos_general.md": {
        "source": "Legislacion chilena y base de conocimiento del proyecto",
        "source_url": "https://www.bcn.cl/leychile/",
        "source_date": "2026-08-18",
    },
    "step3_permisos_recoleta.md": {
        "source": "Municipalidad de Recoleta; base de conocimiento del proyecto",
        "source_url": "https://www.recoleta.cl/",
        "source_date": "2026-08-18",
    },
}


# =====================================================================
# CHUNKING SEMÁNTICO PARA MARKDOWN
# =====================================================================
def split_by_headers(text, header_pattern=r"^(#{1,3})\s+(.*)$"):
    """
    Divide el texto en secciones según encabezados Markdown (#, ##, ###).
    Devuelve una lista de tuplas (header_completo, contenido_de_la_seccion).
    """
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
    """
    Divide en unidades semánticas: párrafos normales (separados por doble
    salto de línea) y, dentro de bloques de lista, cada ítem (bullet o
    numerado) como unidad independiente, para no fusionar listas largas en
    un solo párrafo gigante.
    """
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
    """
    Último recurso: si un párrafo o ítem de lista individual excede el
    tamaño máximo, se corta por oraciones completas (nunca a mitad de
    palabra), con un pequeño overlap que respeta límites de palabra.
    """
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
    """Fusiona chunks muy pequeños (ej. un encabezado suelto sin contenido)
    con el siguiente, para evitar embeddings de fragmentos sin sustancia."""
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
    """
    Chunking semántico para Markdown:
    1. Divide el documento por secciones (##).
    2. Si una sección completa cabe en `size`, queda como un solo chunk.
    3. Si es más larga, se subdivide por párrafos/ítems completos, sin
       cortar nunca a mitad de oración o de bullet.
    4. Los chunks derivados de subdividir una sección larga repiten el
       encabezado de esa sección, para no perder contexto al buscarlos.
    """
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
# METADATA
# =====================================================================
def limpiar_metadata(file_name):
    meta = {"file_name": file_name}

    if "recoleta" in file_name.lower():
        meta["comuna"] = "recoleta"
    elif "bosque" in file_name.lower():
        meta["comuna"] = "el bosque"
    else:
        meta["comuna"] = "general"

    if "step1" in file_name.lower():
        meta["etapa"] = "constitucion"
    elif "step2" in file_name.lower():
        meta["etapa"] = "sii_actividades"
    elif "step3" in file_name.lower():
        meta["etapa"] = "patentes_permisos"
    else:
        meta["etapa"] = "general"

    return meta


def leer_metadata_documento(file_name: str, text: str) -> dict:
    """Combina metadata derivada del nombre con frontmatter del documento.

    Cada documento RAG debe declarar `source_url` y `source_date` para que las
    respuestas puedan informar de dónde proviene la información y cuándo fue
    revisada. El bloque esperado al inicio del Markdown es:

    ---
    source: Municipalidad de Ejemplo
    source_url: https://ejemplo.cl/tramites
    source_date: 2026-08-18
    ---
    """
    meta = {**limpiar_metadata(file_name), **DOCUMENT_METADATA.get(file_name, {})}
    lines = text.splitlines()

    if lines and lines[0].strip() == "---":
        try:
            end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
        except StopIteration as error:
            raise ValueError(f"{file_name}: frontmatter sin cierre") from error

        for line in lines[1:end]:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, separator, value = line.partition(":")
            if separator:
                meta[key.strip()] = value.strip().strip('"\'')

    required = ("source", "source_url", "source_date")
    missing = [key for key in required if not meta.get(key)]
    if missing:
        raise ValueError(
            f"{file_name}: falta metadata obligatoria: {', '.join(missing)}"
        )

    if not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", meta["source_date"]):
        raise ValueError(
            f"{file_name}: source_date debe tener formato YYYY, YYYY-MM o YYYY-MM-DD"
        )

    return meta


# =====================================================================
# SETUP BD
# =====================================================================
def setup_db(conn):
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                id bigserial PRIMARY KEY,
                content text NOT NULL,
                metadata jsonb,
                embedding vector(768)
            )
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS {TABLE_NAME}_embedding_idx
            ON {TABLE_NAME} USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100)
        """)
        conn.commit()
    print("✅ Tabla e índice listos.")


# =====================================================================
# MAIN
# =====================================================================
def main():
    print("🔌 Conectando a Supabase...")
    try:
        conn = psycopg2.connect(DSN)
        print("✅ Conexión OK.")
    except Exception as e:
        print(f"❌ Error de conexión: {e}")
        return

    try:
        setup_db(conn)
    except Exception as e:
        print(f"❌ Error al preparar BD: {e}")
        conn.close()
        return

    print(f"📦 Cargando modelo '{MODELO}'...")
    model = SentenceTransformer(MODELO)
    print("✅ Modelo listo.")

    print(f"📄 Leyendo archivos .md desde '{DATA_DIR}'...")
    if not os.path.exists(DATA_DIR):
        print(f"❌ No existe la carpeta '{DATA_DIR}'.")
        conn.close()
        return

    all_rows = []
    archivos = [f for f in sorted(os.listdir(DATA_DIR)) if f.endswith(".md")]

    if not archivos:
        print("❌ No hay archivos .md.")
        conn.close()
        return

    for file_name in archivos:
        file_path = os.path.join(DATA_DIR, file_name)
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()

        meta = leer_metadata_documento(file_name, text)
        chunks = chunk_text(text)
        print(f"   📎 {file_name} → {len(chunks)} chunks")

        for chunk in chunks:
            all_rows.append((chunk.strip(), meta))

    print(f"\n✅ Total: {len(all_rows)} chunks de {len(archivos)} archivos.")

    print("🔢 Generando embeddings...")
    textos = [row[0] for row in all_rows]
    textos_prefijados = [f"passage: {t}" for t in textos]
    embeddings = model.encode(textos_prefijados, batch_size=32, show_progress_bar=True)
    print("✅ Embeddings generados.")

    print("🚀 Insertando en Supabase...")
    try:
        rows = [
            (textos[i], json.dumps(all_rows[i][1], ensure_ascii=False), embeddings[i].tolist())
            for i in range(len(all_rows))
        ]
        with conn.cursor() as cur:
            execute_values(
                cur,
                f"INSERT INTO {TABLE_NAME} (content, metadata, embedding) VALUES %s",
                rows,
                template="(%s, %s::jsonb, %s::vector)",
            )
            conn.commit()
        print(f"💾 {len(rows)} filas insertadas.")
        print("\n🎉 ¡Pipeline completado! Los datos están en Supabase.")
    except Exception as e:
        print(f"❌ Error al insertar: {e}")
        conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    main()