from core.roadmaps import get_pending_milestone
import logging

import dependencies

logger = logging.getLogger("financial")


def record_roadmap_unsatisfaction(user: dict,) -> bool:
    hito = get_pending_milestone(user)

    if not hito:
        return False

    client = dependencies.supabase_admin or dependencies.supabase

    if client is None:
        logger.warning("No hay cliente Supabase para guardar feedback")
        return False

    try:
        client.table("assistant_feedback").insert({
            "comuna": user.get("comuna"),
            "hito_id": hito.get("id"),
            "hito_title": hito.get("title"),
            "feedback_type": "unhelpful",
        }).execute()
        return True
    except Exception as error:
        logger.error(
            "No se pudo registrar insatisfacción del roadmap: %s",
            error,
        )
        return False