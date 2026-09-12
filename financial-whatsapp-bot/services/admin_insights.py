from collections import Counter

import dependencies
from db.users import get_users_by_comuna

TOPIC_KEYWORDS = {
    "Formalización": (
        "formalizar",
        "formalización",
        "rut",
        "inicio de actividades",
    ),
    "Patentes": (
        "patente",
        "patente comercial",
        "municipalidad",
    ),
    "SII": (
        "sii",
        "iva",
        "boleta",
        "inicio de actividades",
    ),
    "Permisos": (
        "seremi",
        "sanitario",
        "sag",
        "permiso",
    ),
}


def classify_topic(text: str) -> str:
    normalized_text = (text or "").casefold()

    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in normalized_text for keyword in keywords):
            return topic

    return "Otros"


def _empty_insights() -> dict:
    return {
        "no_useful": 0,
        "topics": {},
        "conflictivos": {},
        "rag_gaps": {},
    }

def get_admin_insights(comuna: str) -> dict:
    supabase = dependencies.supabase_admin

    if supabase is None:
        return _empty_insights()

    feedback_result = (
        supabase
        .table("assistant_feedback")
        .select("hito_title, feedback_type")
        .eq("comuna", comuna)
        .eq("feedback_type", "unhelpful")
        .execute()
    )

    conflictive_topics = Counter(
        item.get("hito_title") or "Hito desconocido"
        for item in feedback_result.data or []
    )

    users = get_users_by_comuna(comuna)
    phones = [
        user["phone"]
        for user in users
        if user.get("phone")
    ]

    topics = Counter()

    if phones:
        messages_result = (
            supabase
            .table("messages")
            .select("content")
            .in_("phone", phones)
            .eq("role", "user")
            .execute()
        )

        for item in messages_result.data or []:
            topics[classify_topic(item.get("content") or "")] += 1

    return {
        "no_useful": sum(conflictive_topics.values()),
        "topics": dict(topics.most_common()),
        "conflictivos": dict(conflictive_topics.most_common()),
        "rag_gaps": {},
    }