"""
FinancIAl — services/transcription.py

Transcripción de notas de voz de WhatsApp. Prototipo de factibilidad para
HdU01 (hoy el bot solo avisa "no proceso audio"; esto permite entenderlo
de verdad) — se está probando local antes de decidir si se integra de
forma permanente.

Usa el mismo proveedor compatible con OpenAI que ya configuran en
OLLAMA_URL/IA_API_KEY para el chat (Groq, OpenRouter, etc.): ambos
exponen un endpoint /audio/transcriptions con Whisper en el mismo
formato multipart, así que no hace falta una credencial ni un proveedor
nuevo.
"""
import logging

import httpx

from config import IA_API_KEY, OLLAMA_URL

logger = logging.getLogger("financial")

TRANSCRIPTION_MODEL = "openai/whisper-large-v3-turbo"


def _transcription_endpoint() -> str:
    return f"{OLLAMA_URL.rstrip('/')}/audio/transcriptions"


async def transcribe_audio(audio_bytes: bytes, mime_type: str) -> str | None:
    """Transcribe audio a texto en español.

    Nunca lanza: ante cualquier fallo (red, credencial faltante, respuesta
    vacía) devuelve None, para que el llamador caiga al mensaje genérico
    de "no pude procesar tu audio" sin romper el flujo del webhook.
    """
    if not IA_API_KEY:
        logger.warning("Transcripción de audio deshabilitada: falta IA_API_KEY")
        return None

    headers = {"Authorization": f"Bearer {IA_API_KEY}"}
    files = {"file": ("audio.ogg", audio_bytes, mime_type or "audio/ogg")}
    data = {"model": TRANSCRIPTION_MODEL, "language": "es"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                _transcription_endpoint(), headers=headers, files=files, data=data
            )
            response.raise_for_status()
            result = response.json()
    except httpx.HTTPError as error:
        logger.error("Error transcribiendo audio: %s", error)
        return None

    text = (result.get("text") or "").strip()
    if not text:
        logger.warning("La transcripción de audio devolvió texto vacío")
        return None

    logger.info("Audio transcrito (%d bytes, %d caracteres)", len(audio_bytes), len(text))
    return text
