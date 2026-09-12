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
        "municipalidad",
    ),
    "SII": (
        "sii",
        "iva",
        "boleta",
    ),
    "Permisos": (
        "seremi",
        "sanitario",
        "sag",
        "permiso",
    ),
}
TOPIC_ALIASES = {
    "sii": "SII",
    "formalizacion": "Formalización",
    "patentes": "Patentes",
    "permisos": "Permisos",
    "otros": "Otros",
}


def normalize_topic(topic: str | None) -> str:
    normalized_topic = (topic or "Otros").casefold()
    return TOPIC_ALIASES.get(normalized_topic, topic or "Otros")

def classify_topic(text: str) -> str:
    text = (text or "").casefold()

    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return topic

    return "Otros"


def get_admin_insights(comuna: str) -> dict:
    supabase = dependencies.supabase_admin

    if supabase is None:
        return {
            "no_useful": 0,
            "topics": {},
            "conflictivos": {},
        }

    # Insatisfacciones registradas explícitamente.
    feedback = (
        supabase
        .table("assistant_feedback")
        .select("topic, hito_title")
        .eq("comuna", comuna)
        .eq("feedback_type", "unhelpful")
        .execute()
        .data
        or []
    )

    topics = Counter(
    normalize_topic(item.get("topic"))
    for item in feedback
    )

    conflictivos = Counter(
        item.get("hito_title") or "Hito desconocido"
        for item in feedback
    )

    # Temas de todas las consultas guardadas de la comuna.
    usuarios = get_users_by_comuna(comuna)
    phones = [
        usuario["phone"]
        for usuario in usuarios
        if usuario.get("phone")
    ]

    if phones:
        messages = (
            supabase
            .table("messages")
            .select("content")
            .in_("phone", phones)
            .eq("role", "user")
            .execute()
            .data
            or []
        )

        for message in messages:
            topic = normalize_topic(
                classify_topic(message.get("content", ""))
            )
            topics[topic] += 1

    return {
        "no_useful": len(feedback),
        "topics": dict(topics.most_common()),
        "conflictivos": dict(conflictivos.most_common()),
    }