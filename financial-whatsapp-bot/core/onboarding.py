import copy
from datetime import datetime

from core.roadmaps import ROADMAPS


RUBRO_KEYWORDS = {
    "textil": ["textil", "ropa", "confección", "confeccion", "costura", "tela", "lenceria", "lencería", "jeans", "polera"],
    "alimentos": ["alimento", "comida", "cocina", "gastronomía", "gastronomia", "snack", "dulce", "chocolate", "pastel", "torta", "pan", "empanada", "cocinar"],
    "joyeria": ["joya", "joyería", "joyeria", "plata", "anillo", "collar", "pulsera", "artesanía", "artesania", "bisutería", "bisuteria", "febrería", "febreria"],
}


def detect_rubro(text: str) -> str:
    """Detect rubro from free text."""
    text_lower = text.lower().strip()
    for rubro, keywords in RUBRO_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return rubro
    return "otro"


def detect_sii(text: str) -> str | None:
    """Detect SII status from free text."""
    text_lower = text.lower().strip()
    positive = ["si", "sí", "ya", "listo", "hecho", "tengo", "formalizado", "formalizada"]
    negative = ["no", "todavía", "todavia", "aún", "aun", "nada", "nunca"]
    unknown = ["no sé", "no se", "qué es", "que es"]

    for kw in unknown:
        if kw in text_lower:
            return "no_sabe"
    for kw in positive:
        if kw in text_lower:
            return "si"
    for kw in negative:
        if kw in text_lower:
            return "no"
    return None


def process_onboarding(user: dict, message: str, save_user_fn) -> str:
    raw = user.get("onboarding_step", 0)
    try:
        step = int(raw)
    except (ValueError, TypeError):
        step = raw  # mantiene "done" como string

    if step == 0:
        user["onboarding_step"] = 1  # ✅ corregido
        save_user_fn(user["phone"], user)
        return (
            "¡Hola! 👋 Soy *FinancIAl*, tu asistente para formalizar y hacer crecer tu emprendimiento.\n\n"
            "Voy a hacerte *3 preguntas rápidas* para personalizar tu experiencia.\n\n"
            "📌 *Pregunta 1 de 3:*\n"
            "¿En qué rubro está tu emprendimiento?\n\n"
            "Ejemplo: _textil, alimentos, joyería, etc._"
        )
    if step == 1:
        rubro = detect_rubro(message)
        user["rubro"] = rubro
        user["rubro_raw"] = message.strip()
        user["onboarding_step"] = 2
        save_user_fn(user["phone"], user)

        rubro_display = user["rubro_raw"] if user["rubro"] == "otro" else user["rubro"].capitalize()
        return (
            f"✅ Rubro: *{rubro_display}*\n\n"
            "📍 *Pregunta 2 de 3:*\n"
            "¿En qué comuna trabajas?\n\n"
            "Ejemplo: _Recoleta, El Bosque, Santiago, etc._"
        )
    if step == 2:
        user["comuna"] = message.strip().title()
        user["onboarding_step"] = 3
        save_user_fn(user["phone"], user)

        return (
            f"✅ Comuna: *{user['comuna']}*\n\n"
            "📍 *Pregunta 3 de 3:*\n"
            "¿Tu emprendimiento está formalizado en el SII?\n\n"
            "Ejemplo: _si_, _no_, _no sé que es eso_"
        )

    if step == 3:
        sii = detect_sii(message)
        if sii is None:
            return (
                "No entendí tu respuesta. Por favor responde con _si_, _no_ o _no sé que es eso_.\n\n"
                "¿Tu emprendimiento está formalizado en el SII?"
            )
        user["inicio_sii"] = sii if sii != "no_sabe" else "no"
        user["onboarding_step"] = "done"

        # Generate roadmap
        if sii == "si":
            roadmap_key = "formalizado"
        else:
            roadmap_key = user.get("rubro", "otro")

        user["roadmap"] = copy.deepcopy(ROADMAPS.get(roadmap_key, ROADMAPS["otro"]))
        user["conversation_history"] = []
        user["created_at"] = datetime.utcnow().isoformat()
        save_user_fn(user["phone"], user)

        total = len(user["roadmap"])
        rubro_display = user.get("rubro_raw", user.get("rubro", "")).capitalize()
        estado = "Formalizado ✅" if sii == "si" else "No formalizado"

        sii_explain = ""
        if sii == "no_sabe":
            sii_explain = "\n\n💡 _El inicio de actividades es el trámite que registra tu negocio ante el SII. Sin esto, no puedes emitir boletas ni facturas. ¡Pero no te preocupes, te voy a guiar paso a paso!_\n"

        return (
            f"🎉 *¡Perfecto! Ya tengo tu perfil:*\n\n"
            f"📌 Rubro: *{rubro_display}*\n"
            f"📍 Comuna: *{user['comuna']}*\n"
            f"📋 Estado SII: *{estado}*\n"
            f"{sii_explain}\n"
            f"Te preparé un *roadmap personalizado* con *{total} hitos* para "
            f"{'hacer crecer' if sii == 'si' else 'formalizar'} tu negocio.\n\n"
            "¿Qué quieres hacer ahora? Escribe:\n\n"
            "📋 *\"mi roadmap\"* → ver tus pasos\n"
            "🎯 *\"postular a fondo\"* → simular postulación\n"
            "💬 O simplemente *hazme cualquier pregunta* sobre tu negocio"
        )

    return None