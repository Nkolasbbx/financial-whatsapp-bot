"""
Microservicio de embeddings - proyecto independiente.

Mantiene el modelo de embeddings cargado en memoria (igual que hacía
`dependencies.embedding_model` en el bot original), pero como servicio
propio que corre 24/7 en Railway/Fly.io, en vez de dentro del proceso
serverless del bot (Vercel).

El bot le hace un httpx.post() a este servicio en vez de cargar el
modelo localmente.
"""

import os
import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("embedding-service")

# Mismo modelo que ya usabas en dependencies.embedding_model.
# Se carga UNA vez al iniciar el proceso, y queda en memoria mientras
# el servicio esté vivo (por eso necesita correr en un host persistente,
# no serverless).
MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "intfloat/multilingual-e5-base")

logger.info(f"🔄 Cargando modelo de embeddings: {MODEL_NAME}...")
model = SentenceTransformer(MODEL_NAME)
logger.info("✅ Modelo cargado y listo.")

app = FastAPI(title="Embedding Service", version="1.0.0")


class EmbedRequest(BaseModel):
    text: str
    prefix: str = "query"  # "query" para preguntas, "passage" para documentos a indexar


class EmbedResponse(BaseModel):
    embedding: list[float]
    dimensions: int


@app.get("/health")
def health():
    """Usado para pings de keep-alive y para verificar que el modelo esté cargado."""
    return {"status": "ok", "model": MODEL_NAME}


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest):
    """
    Genera el embedding de un texto.
    Usa el mismo prefijo "query:"/"passage:" que requiere la familia de
    modelos e5, igual que en tu código original:
        dependencies.embedding_model.encode(f"query: {message}")
    """
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="El campo 'text' no puede estar vacío.")

    try:
        texto_con_prefijo = f"{req.prefix}: {req.text}"
        vector = model.encode(texto_con_prefijo).tolist()
        return EmbedResponse(embedding=vector, dimensions=len(vector))
    except Exception as e:
        logger.error(f"💥 Error generando embedding: {e}")
        raise HTTPException(status_code=500, detail="Error interno generando el embedding.")


class EmbedBatchRequest(BaseModel):
    texts: list[str]
    prefix: str = "passage"  # útil para re-embeber varios documentos de una vez


@app.post("/embed/batch")
def embed_batch(req: EmbedBatchRequest):
    """Genera embeddings para varios textos a la vez (útil para re-indexar documentos)."""
    if not req.texts:
        raise HTTPException(status_code=400, detail="La lista 'texts' no puede estar vacía.")

    try:
        textos_con_prefijo = [f"{req.prefix}: {t}" for t in req.texts]
        vectores = model.encode(textos_con_prefijo).tolist()
        return {"embeddings": vectores, "count": len(vectores)}
    except Exception as e:
        logger.error(f"💥 Error generando embeddings en batch: {e}")
        raise HTTPException(status_code=500, detail="Error interno generando embeddings.")