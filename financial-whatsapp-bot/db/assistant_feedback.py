from core.roadmaps import get_pending_milestone
import logging

import dependencies

logger = logging.getLogger("financial")

TOPIC_KEYWORDS = {
    "Formalización": ("formalizar", "rut", "inicio de actividades"),
    "Patentes": ("patente", "municipalidad"),
    "SII": ("sii", "iva", "boleta"),
    "Permisos": ("seremi", "sanitario", "sag", "permiso"),
}


def classify_topic(text: str) -> str:
    normalized_text = (text or "").casefold()

    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in normalized_text for keyword in keywords):
            return topic

    return "Otros"

def record_unsatisfaction(user: dict, message: str) -> bool:
    hito = get_pending_milestone(user)

    if not hito:
        return False

    client = dependencies.supabase_admin or dependencies.supabase

    if client is None:
        logger.warning("No hay cliente Supabase para guardar feedback")
        return False

    topic = classify_topic(message)

    try:
        client.table("assistant_feedback").insert({
            "comuna": user.get("comuna"),
            "hito_id": hito.get("id"),
            "hito_title": hito.get("title"),
            "topic": topic,
            "feedback_type": "unhelpful",
        }).execute()
        return True
    except Exception as error:
        logger.error(
            "No se pudo registrar insatisfacción del roadmap: %s",
            error,
        )
        return False