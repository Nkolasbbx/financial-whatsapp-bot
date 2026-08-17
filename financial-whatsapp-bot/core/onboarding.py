import copy
import re
from datetime import datetime

from core.roadmaps import ROADMAPS


# Vocabulario completo que el sistema sabe reconocer. Puede seguir
# creciendo aunque el rubro todavía no esté habilitado para elegir
# (ver RUBROS_ACTIVOS).
RUBRO_KEYWORDS = {
    "textil": ["textil", "ropa", "confección", "confeccion", "costura", "tela", "lenceria", "lencería", "jeans", "polera"],
    "alimentos": ["alimento", "comida", "cocina", "gastronomía", "gastronomia", "snack", "dulce", "chocolate", "pastel", "torta", "pan", "empanada", "cocinar"],
    "joyeria": ["joya", "joyería", "joyeria", "plata", "anillo", "collar", "pulsera", "artesanía", "artesania", "bisutería", "bisuteria", "febrería", "febreria"],
}

# Rubros habilitados para elegir en el onboarding HOY. Para escalar a un
# rubro nuevo: agrega su id aquí (su roadmap ya debe existir en
# core/roadmaps.py y sus keywords en RUBRO_KEYWORDS).
RUBROS_ACTIVOS = ["textil", "alimentos"]

RUBRO_DISPLAY = {
    "textil": "Textil",
    "alimentos": "Alimentos",
    "joyeria": "Joyería",
}

# ids usados en los botones/listas interactivas de Meta, generados solo
# a partir de los rubros activos.
RUBRO_OPTIONS = [
    (f"rubro_{rubro}", RUBRO_DISPLAY[rubro])
    for rubro in RUBROS_ACTIVOS
]

SII_OPTIONS = [
    ("sii_si", "Sí"),
    ("sii_no", "No"),
    ("sii_no_sabe", "No sé"),
]

# Comunas soportadas por el negocio. Cerrado a propósito: no se atiende
# fuera de esta cobertura.
COMUNA_OPTIONS = [
    ("comuna_recoleta", "Recoleta"),
    ("comuna_el_bosque", "El Bosque"),
]
COMUNA_DISPLAY = {
    "comuna_recoleta": "Recoleta",
    "comuna_el_bosque": "El Bosque",
}

AMBIGUOUS_PHRASES = {
    "hola", "hi", "hello", "hey", "buenas", "buenas tardes", "buenas noches",
    "buenos dias", "buenos días", "que tal", "qué tal", "ola",
    "ok", "okay", "vale", "bien", "gracias", "de nada", "listo",
    "?", "??", "...",
}

BACK_PHRASES = {
    "atras", "atrás", "volver", "anterior", "regresar", "retroceder",
    "paso anterior", "volver atras", "volver atrás",
}

VOLVER_HINT = '\n\n_Escribe "volver" si quieres corregir la pregunta anterior._'


def is_ambiguous(text: str) -> bool:
    """Detecta saludos, confirmaciones sueltas o mensajes demasiado cortos/vacíos."""
    cleaned = (text or "").strip().lower()
    if not cleaned:
        return True
    if not re.search(r"[a-záéíóúñ0-9]", cleaned):
        return True
    if cleaned in AMBIGUOUS_PHRASES:
        return True
    return False


def is_back_command(text: str) -> bool:
    """Detecta si el usuario quiere volver a la pregunta anterior."""
    return (text or "").strip().lower() in BACK_PHRASES


def detect_rubro(text: str) -> str | None:
    """Detecta rubro desde texto libre o desde un id de botón/lista (rubro_*).

    Solo devuelve un rubro si está en RUBROS_ACTIVOS. Un rubro reconocido
    en RUBRO_KEYWORDS pero no activo (ej. joyeria) se trata igual que uno
    desconocido: devuelve None y no se guarda.
    """
    text_lower = (text or "").lower().strip()

    if text_lower.startswith("rubro_"):
        candidate = text_lower.removeprefix("rubro_")
        return candidate if candidate in RUBROS_ACTIVOS else None

    for rubro in RUBROS_ACTIVOS:
        for kw in RUBRO_KEYWORDS.get(rubro, []):
            if kw in text_lower:
                return rubro

    return None


def detect_comuna(text: str) -> str | None:
    """Detecta comuna desde un id de botón (comuna_*) o desde texto libre.

    Solo se aceptan las comunas en COMUNA_OPTIONS. Cualquier otra cosa
    devuelve None y NO debe guardarse.
    """
    text_lower = (text or "").lower().strip()

    if text_lower in COMUNA_DISPLAY:
        return text_lower

    normalized = text_lower.replace("í", "i")
    if normalized in {"recoleta"}:
        return "comuna_recoleta"
    if normalized in {"el bosque", "bosque"}:
        return "comuna_el_bosque"

    return None


def detect_sii(text: str) -> str | None:
    """Detect SII status from free text or desde un id de botón (sii_*)."""
    text_lower = (text or "").lower().strip()

    if text_lower == "sii_si":
        return "si"
    if text_lower == "sii_no":
        return "no"
    if text_lower == "sii_no_sabe":
        return "no_sabe"

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


def _text(body: str) -> dict:
    return {"type": "text", "body": body}


def _buttons(body: str, options: list[tuple[str, str]]) -> dict:
    return {"type": "buttons", "body": body, "options": options}


def _list(body: str, button_text: str, options: list[tuple[str, str]]) -> dict:
    return {"type": "list", "body": body, "button_text": button_text, "options": options}


def _prompt_step_1(prefix: str = "") -> dict:
    body = (
        prefix
        + "📌 *Pregunta 1 de 3:*\n"
        "¿En qué rubro está tu emprendimiento?"
        + VOLVER_HINT
    )
    return _list(body, "Elegir rubro", RUBRO_OPTIONS)


def _prompt_step_2(prefix: str = "") -> dict:
    body = (
        prefix
        + "📍 *Pregunta 2 de 3:*\n"
        "¿En qué comuna trabajas?\n\n"
        "Por ahora solo atendemos *Recoleta* y *El Bosque*."
        + VOLVER_HINT
    )
    return _buttons(body, COMUNA_OPTIONS)


def _prompt_step_3(prefix: str = "") -> dict:
    body = (
        prefix
        + "📋 *Pregunta 3 de 3:*\n"
        "¿Tu emprendimiento está formalizado en el SII?"
        + VOLVER_HINT
    )
    return _buttons(body, SII_OPTIONS)


def process_onboarding(user: dict, message: str, save_user_fn):
    """Devuelve un dict {type, body, options?, button_text?} describiendo
    qué enviar al usuario. type puede ser: text | buttons | list.

    Mientras el onboarding está en curso, SOLO se aceptan: respuestas
    válidas del paso actual (texto libre o id de botón/lista), o el
    comando de retroceso ("volver"). Cualquier otro mensaje se rechaza
    y se repite la pregunta actual sin guardar ni avanzar.
    """
    raw = user.get("onboarding_step", 0)
    try:
        step = int(raw)
    except (ValueError, TypeError):
        step = raw  # mantiene "done" como string

    if step == 0:
        user["onboarding_step"] = 1
        save_user_fn(user["phone"], user)
        prompt = _prompt_step_1()
        prompt["body"] = (
            "¡Hola! 👋 Soy *FinancIAl*, tu asistente para formalizar y hacer crecer tu emprendimiento.\n\n"
            "Voy a hacerte *3 preguntas rápidas* para personalizar tu experiencia.\n\n"
            + prompt["body"]
        )
        return prompt

    if step == 1:
        # No hay paso anterior al que volver desde aquí.
        if is_back_command(message):
            return _prompt_step_1("Esta es la primera pregunta, no hay un paso anterior 🙂\n\n")

        if is_ambiguous(message):
            return _prompt_step_1("No logré identificar tu rubro 🤔\n\n")

        rubro = detect_rubro(message)
        if rubro is None:
            return _prompt_step_1(
                "Por ahora solo trabajamos con *textil* y *alimentos* 🙏 "
                "Elige una opción de la lista.\n\n"
            )

        user["rubro"] = rubro
        user["rubro_raw"] = message.strip()
        user["onboarding_step"] = 2
        save_user_fn(user["phone"], user)

        rubro_display = RUBRO_DISPLAY.get(rubro, rubro.capitalize())
        return _prompt_step_2(f"✅ Rubro: *{rubro_display}*\n\n")

    if step == 2:
        if is_back_command(message):
            user["onboarding_step"] = 1
            save_user_fn(user["phone"], user)
            return _prompt_step_1()

        if is_ambiguous(message):
            return _prompt_step_2("No logré identificar tu comuna 🤔\n\n")

        comuna_id = detect_comuna(message)
        if comuna_id is None:
            return _prompt_step_2(
                "Por ahora no trabajamos en esa comuna 😕 "
                "Elige una de las opciones disponibles e inténtalo de nuevo.\n\n"
            )

        user["comuna"] = COMUNA_DISPLAY[comuna_id]
        user["onboarding_step"] = 3
        save_user_fn(user["phone"], user)

        return _prompt_step_3(f"✅ Comuna: *{user['comuna']}*\n\n")

    if step == 3:
        if is_back_command(message):
            user["onboarding_step"] = 2
            save_user_fn(user["phone"], user)
            return _prompt_step_2()

        sii = detect_sii(message)
        if sii is None:
            return _prompt_step_3("No entendí tu respuesta 🤔\n\n")

        user["inicio_sii"] = sii if sii != "no_sabe" else "no"
        user["onboarding_step"] = "done"

        if sii == "si":
            roadmap_key = "formalizado"
        else:
            roadmap_key = user.get("rubro", "otro")

        user["roadmap"] = copy.deepcopy(ROADMAPS.get(roadmap_key, ROADMAPS["otro"]))
        user["conversation_history"] = []
        user["created_at"] = datetime.utcnow().isoformat()
        save_user_fn(user["phone"], user)

        total = len(user["roadmap"])
        rubro_display = RUBRO_DISPLAY.get(user.get("rubro"), user.get("rubro_raw", "").capitalize())
        estado = "Formalizado ✅" if sii == "si" else "No formalizado"

        sii_explain = ""
        if sii == "no_sabe":
            sii_explain = (
                "\n\n💡 _El inicio de actividades es el trámite que registra tu "
                "negocio ante el SII. Sin esto, no puedes emitir boletas ni "
                "facturas. ¡Pero no te preocupes, te voy a guiar paso a paso!_\n"
            )

        body = (
            f"🎉 *¡Perfecto! Ya tengo tu perfil:*\n\n"
            f"📌 Rubro: *{rubro_display}*\n"
            f"📍 Comuna: *{user['comuna']}*\n"
            f"📋 Estado SII: *{estado}*\n"
            f"{sii_explain}\n"
            f"Te preparé un *roadmap personalizado* con *{total} hitos* para "
            f"{'hacer crecer' if sii == 'si' else 'formalizar'} tu negocio.\n\n"
            "¿Qué quieres hacer ahora?"
        )

        return _buttons(
            body,
            [
                ("menu_roadmap", "📋 Ver mi roadmap"),
                ("menu_fondo", "🎯 Postular a fondo"),
                ("menu_recordatorios_on", "🔔 Activar recordatorios"),
            ],
        )

    return None