import copy
import re
from datetime import datetime

from core.menu import get_menu_widget
from core.roadmaps import ROADMAPS

# Rubros activos y palabras clave
RUBRO_KEYWORDS = {
    "textil": ["textil", "ropa", "confección", "confeccion", "costura", "tela", "lenceria", "lencería", "jeans", "polera", "estampado", "taller textil"],
    "alimentos": ["alimento", "alimentos", "comida", "cocina", "gastronomía", "gastronomia", "snack", "dulce", "chocolate", "pastel", "torta", "pan", "empanada", "cocinar", "reposteria", "repostería"],
}

RUBROS_ACTIVOS = ["textil", "alimentos"]

RUBRO_DISPLAY = {
    "textil": "Textil",
    "alimentos": "Alimentos",
}

# Paso 1: 2 botones
RUBRO_OPTIONS = [
    ("rubro_textil", "🧵 Textil"),
    ("rubro_alimentos", "🍲 Alimentos"),
]

# Paso 2: 3 botones
COMUNA_OPTIONS = [
    ("comuna_recoleta", "Recoleta"),
    ("comuna_el_bosque", "El Bosque"),
    ("volver_step", "↩️ Volver"),
]

COMUNA_DISPLAY = {
    "comuna_recoleta": "Recoleta",
    "comuna_el_bosque": "El Bosque",
}

# Paso 3: Lista interactiva para soportar opciones + Volver sin exceder el límite
SII_LIST_OPTIONS = [
    ("sii_si", "✅ Sí, ya tengo inicio de actividades"),
    ("sii_no", "❌ No, aún no me formalizo"),
    ("sii_no_sabe", "❓ No sé / No estoy seguro"),
    ("volver_step", "↩️ Volver a comuna"),
]

AMBIGUOUS_PHRASES = {
    "hola", "hi", "hello", "hey", "buenas", "buenas tardes", "buenas noches",
    "buenos dias", "buenos días", "que tal", "qué tal", "ola",
    "ok", "okay", "vale", "bien", "gracias", "de nada", "listo",
    "?", "??", "...",
}

BACK_PHRASES = {
    "atras", "atrás", "volver", "anterior", "regresar", "retroceder",
    "paso anterior", "volver atras", "volver atrás", "volver_step",
}

VOLVER_HINT = '\n\n_Toca "↩️ Volver" o escribe "volver" si deseas corregir la pregunta anterior._'

SII_EXPLANATION = (
    '💡 *¿Qué es el "inicio de actividades" (estar formalizado)?*\n\n'
    "Es el aviso que le das al SII (Servicio de Impuestos Internos) de que tu emprendimiento "
    "ya funciona como un negocio. Al hacerlo puedes:\n"
    "• Emitir boletas o facturas a tus clientes\n"
    "• Postular a créditos, fondos concursables y licitaciones del Estado\n"
    "• Evitar multas por operar sin estar registrado\n\n"
    'Si *todavía no lo hiciste*, estás "no formalizado" — es súper común, y para eso te '
    "armamos una ruta paso a paso. 🚀\n\n"
)


def is_ambiguous(text: str) -> bool:
    cleaned = (text or "").strip().lower()
    if not cleaned:
        return True
    if not re.search(r"[a-záéíóúñ0-9]", cleaned):
        return True
    if cleaned in AMBIGUOUS_PHRASES:
        return True
    return False


def is_back_command(text: str) -> bool:
    return (text or "").strip().lower() in BACK_PHRASES


def detect_rubro(text: str) -> str | None:
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
    text_lower = (text or "").lower().strip()
    if text_lower == "sii_si":
        return "si"
    if text_lower == "sii_no":
        return "no"
    if text_lower == "sii_no_sabe":
        return "no_sabe"

    positive = ["si", "sí", "ya", "listo", "hecho", "tengo", "formalizado", "formalizada", "inicio"]
    negative = ["no", "todavía", "todavia", "aún", "aun", "nada", "nunca", "informal"]
    unknown = ["no sé", "no se", "qué es", "que es", "duda", "no estoy seguro"]

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


def _buttons(body: str, options: list[tuple[str, str]]) -> dict:
    return {"type": "buttons", "body": body, "options": options}


def _list(body: str, button_text: str, options: list[tuple[str, str]]) -> dict:
    return {"type": "list", "body": body, "button_text": button_text, "options": options}


def _prompt_step_1(prefix: str = "") -> dict:
    body = (
        prefix
        + "📌 *Pregunta 1 de 3:*\n"
        + "¿En qué rubro está tu emprendimiento?\n\n"
        + "Por ahora solo trabajamos con *Textil* y *Alimentos*."
    )
    return _buttons(body, RUBRO_OPTIONS)


def _prompt_step_2(prefix: str = "") -> dict:
    body = (
        prefix
        + "📍 *Pregunta 2 de 3:*\n"
        + "¿En qué comuna trabajas?\n\n"
        + "Nuestra cobertura actual es *Recoleta* y *El Bosque*."
        + VOLVER_HINT
    )
    return _buttons(body, COMUNA_OPTIONS)


def _prompt_step_3(prefix: str = "") -> dict:
    body = (
        prefix
        + "📋 *Pregunta 3 de 3:*\n"
        + "¿Tu emprendimiento ya cuenta con inicio de actividades formalizado en el SII?\n\n"
        + "Toca el botón *'Seleccionar estado'* abajo para ver las opciones."
    )
    return _list(body, "Seleccionar estado", SII_LIST_OPTIONS)


def _finalize_onboarding(user: dict, sii: str, save_user_fn) -> dict:
    """Guarda inicio_sii ya resuelto ('si'/'no') y arma el cierre del onboarding + roadmap."""
    user["inicio_sii"] = sii
    user["onboarding_step"] = "done"
    user["conversation_history"] = []
    user["created_at"] = datetime.utcnow().isoformat()

    rubro_disp = RUBRO_DISPLAY.get(user.get("rubro"), user.get("rubro_raw", "").capitalize())
    comuna_disp = user.get("comuna", "tu comuna")

    # Caso formalizado (sin lista de hitos)
    if sii == "si":
        user["roadmap"] = []
        user["roadmap_completed_at"] = datetime.utcnow().isoformat()
        save_user_fn(user["phone"], user)

        felicitacion = (
            f"🎉 *¡FELICITACIONES! Perfil completado con éxito.*\n\n"
            f"Tu negocio de *{rubro_disp}* en *{comuna_disp}* ya está formalizado ante el SII. 🏢👏\n\n"
            f"Bienvenido/a a *FinancIAl*.\n\n"
        )
    # Caso no formalizado (asigna hoja de ruta por rubro)
    else:
        roadmap_key = user.get("rubro", "otro")
        user["roadmap"] = copy.deepcopy(ROADMAPS.get(roadmap_key, ROADMAPS["otro"]))
        save_user_fn(user["phone"], user)

        total = len(user["roadmap"])
        felicitacion = (
            f"🎉 *¡FELICITACIONES! Perfil completado con éxito.*\n\n"
            f"📌 Rubro: *{rubro_disp}*\n"
            f"📍 Comuna: *{comuna_disp}*\n\n"
            f"Diseñamos una ruta personalizada de *{total} pasos* para formalizar tu negocio.\n\n"
        )

    return get_menu_widget(user, prefix=felicitacion)


def process_onboarding(user: dict, message: str, save_user_fn) -> dict:
    raw = user.get("onboarding_step", 0)
    try:
        step = int(raw)
    except (ValueError, TypeError):
        step = raw

    # ── Inicio del Onboarding ──
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

    # ── Paso 1: Rubro ──
    if step == 1:
        if is_back_command(message):
            return _prompt_step_1("Esta es la primera pregunta, no hay un paso anterior 🙂\n\n")

        rubro = detect_rubro(message)
        if rubro is None:
            guia_error = (
                "⚠️ *Opción no válida.*\n"
                "Por el momento solo trabajamos con *Textil* o *Alimentos*.\n\n"
                "👉 *Elige una opción:* Presiona un botón o escribe textualmente *'Textil'* o *'Alimentos'*.\n\n"
            )
            return _prompt_step_1(guia_error)

        user["rubro"] = rubro
        user["rubro_raw"] = message.strip()
        user["onboarding_step"] = 2
        save_user_fn(user["phone"], user)

        rubro_display = RUBRO_DISPLAY.get(rubro, rubro.capitalize())
        return _prompt_step_2(f"✅ Rubro seleccionado: *{rubro_display}*\n\n")

    # ── Paso 2: Comuna ──
    if step == 2:
        if is_back_command(message):
            user["onboarding_step"] = 1
            save_user_fn(user["phone"], user)
            return _prompt_step_1("↩️ *Volviste a la Pregunta 1 (Rubro).*\n\n")

        comuna_id = detect_comuna(message)
        if comuna_id is None:
            guia_error = (
                "⚠️ *Comuna no soportada.*\n"
                "Por ahora solo atendemos en *Recoleta* y *El Bosque*.\n\n"
                "👉 *Elige una opción:* Presiona un botón o escribe textualmente *'Recoleta'* o *'El Bosque'* (o *'volver'* para cambiar de rubro).\n\n"
            )
            return _prompt_step_2(guia_error)

        user["comuna"] = COMUNA_DISPLAY[comuna_id]
        user["onboarding_step"] = 3
        save_user_fn(user["phone"], user)

        return _prompt_step_3(f"✅ Comuna seleccionada: *{user['comuna']}*\n\n")

    # ── Paso 3: Estado SII ──
    if step == 3:
        if is_back_command(message):
            user["onboarding_step"] = 2
            save_user_fn(user["phone"], user)
            return _prompt_step_2("↩️ *Volviste a la Pregunta 2 (Comuna).*\n\n")

        sii = detect_sii(message)
        if sii is None:
            guia_error = (
                "⚠️ *Respuesta no comprendida.*\n"
                "Necesitamos saber tu estado ante el Servicio de Impuestos Internos.\n\n"
                "👉 *Ejemplos de respuesta:*\n"
                "• Toca el botón *'Seleccionar estado'* y elige una opción de la lista.\n"
                "• O escribe por mensaje: *'Sí'*, *'No'*, *'No sé'*, o *'volver'*.\n\n"
            )
            return _prompt_step_3(guia_error)

        if sii == "no_sabe":
            user["onboarding_step"] = "3_explicado"
            save_user_fn(user["phone"], user)
            return _prompt_step_3(SII_EXPLANATION)

        return _finalize_onboarding(user, sii, save_user_fn)

    # ── Paso 3b: ya vio la explicación de "inicio de actividades", espera respuesta real ──
    if step == "3_explicado":
        if is_back_command(message):
            user["onboarding_step"] = 2
            save_user_fn(user["phone"], user)
            return _prompt_step_2("↩️ *Volviste a la Pregunta 2 (Comuna).*\n\n")

        sii = detect_sii(message)
        if sii is None:
            guia_error = (
                "⚠️ *Respuesta no comprendida.*\n"
                "Necesitamos saber tu estado ante el Servicio de Impuestos Internos.\n\n"
                "👉 *Ejemplos de respuesta:*\n"
                "• Toca el botón *'Seleccionar estado'* y elige una opción de la lista.\n"
                "• O escribe por mensaje: *'Sí'*, *'No'*, o *'volver'*.\n\n"
            )
            return _prompt_step_3(guia_error)

        # Ya vio la explicación: si sigue sin saber, lo registramos como no formalizado en vez
        # de dejarlo en un loop — el criterio de aceptación solo exige explicar antes de
        # registrar, no obligar a que responda con certeza.
        resolved_sii = "no" if sii == "no_sabe" else sii
        return _finalize_onboarding(user, resolved_sii, save_user_fn)

    return get_menu_widget(user)