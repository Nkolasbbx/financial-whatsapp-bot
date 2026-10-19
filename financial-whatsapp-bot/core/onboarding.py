import copy
import logging
import re
from datetime import datetime, timezone

from core.menu import get_menu_widget
from core.profile_validation import (
    PROFILE_VALIDATORS,
    ProfileValidationError,
    has_valid_profile_value,
)
from core.roadmaps import ROADMAPS

logger = logging.getLogger("financial")

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
    for kw in negative:
        if re.search(rf"\b{re.escape(kw)}\b", text_lower):
            return "no"
    for kw in positive:
        if re.search(rf"\b{re.escape(kw)}\b", text_lower):
            return "si"
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


PROFILE_FIELDS = ("nombre_completo", "rut", "email")
PROFILE_BACK_ID = "profile_back"
# Se conserva el identificador para responder a botones de mensajes antiguos.
PROFILE_SKIP_ADDRESS_ID = "profile_skip_address"
PROFILE_BACK_OPTIONS = [(PROFILE_BACK_ID, "↩️ Volver")]

_PROFILE_QUESTIONS = {
    "nombre_completo": "¿Cuál es tu nombre completo?\nPor ejemplo: María González.",
    "rut": "¿Cuál es tu RUT personal?\nIncluye el dígito verificador, por ejemplo: 12.345.678-5.",
    "email": "¿Cuál es tu correo electrónico?\nPor ejemplo: nombre@ejemplo.cl.",
}


def _next_profile_field(user: dict) -> str | None:
    for field in PROFILE_FIELDS:
        if not has_valid_profile_value(user, field):
            return field
    return None


def needs_profile_completion(user: dict) -> bool:
    return (
        user.get("profile_completion_step") in (*PROFILE_FIELDS, "direccion_negocio")
        or _next_profile_field(user) is not None
    )


def _next_business_step(user: dict) -> str | None:
    if not (user.get("rubro") or "").strip():
        return "1"
    if not (user.get("comuna") or "").strip():
        return "2"
    if user.get("inicio_sii") not in {"si", "no"}:
        return "3"
    return None


def needs_onboarding(user: dict) -> bool:
    return (
        user.get("onboarding_step") != "done"
        or needs_profile_completion(user)
        or _next_business_step(user) is not None
    )


def _profile_prompt(field: str, prefix: str = "") -> dict:
    options = [] if field == "nombre_completo" else list(PROFILE_BACK_OPTIONS)
    body = prefix + "👤 *Completemos tu perfil*\n\n" + _PROFILE_QUESTIONS[field]
    return _buttons(body, options) if options else {"type": "text", "body": body}


def _business_prompt(step: str, prefix: str = "") -> dict:
    return {"1": _prompt_step_1, "2": _prompt_step_2, "3": _prompt_step_3}[step](prefix)


def _finalize_onboarding(user: dict) -> dict:
    """Se ejecuta en un borrador; el guardado y la confirmación son atómicos por paso."""
    user["profile_completion_step"] = "done"
    if user.get("onboarding_step") == "done":
        return get_menu_widget(user, prefix="✅ *Datos de tu perfil completados.*\n\nConservamos tu historial y tus avances.\n\n")

    user["onboarding_step"] = "done"
    now = datetime.now(timezone.utc).isoformat()
    if not user.get("created_at"):
        user["created_at"] = now
    # Los perfiles antiguos pueden traer una ruta en curso o ya completada.
    # Completar sus datos no permite regenerarla, aun si ahora responden 'sí'.
    if user.get("roadmap") is None and not user.get("roadmap_completed_at"):
        if user["inicio_sii"] == "si":
            user["roadmap"] = []
            user["roadmap_completed_at"] = now
        else:
            user["roadmap"] = copy.deepcopy(ROADMAPS.get(user["rubro"], ROADMAPS["otro"]))

    prefix = "🎉 *¡Perfil completado con éxito!*\n\n"
    if user["inicio_sii"] == "si":
        prefix += "Tu negocio ya cuenta con inicio de actividades en el SII.\n\n"
    else:
        prefix += f"Tu ruta tiene *{len(user.get('roadmap') or [])} pasos* para formalizar tu negocio.\n\n"
    return get_menu_widget(user, prefix=prefix)


def _continue_onboarding(user: dict, prefix: str = "") -> dict:
    field = _next_profile_field(user)
    if field:
        user["profile_completion_step"] = field
        return _profile_prompt(field, prefix)
    user["profile_completion_step"] = "done"
    step = _next_business_step(user)
    if step:
        user["onboarding_step"] = step
        return _business_prompt(step, prefix)
    return _finalize_onboarding(user)


def _continue_business(user: dict) -> dict:
    # Mantener la secuencia de usuarios que ya estaban en las tres preguntas
    # anteriores. Al terminarlas se solicitan sus datos personales faltantes.
    step = _next_business_step(user)
    if step:
        user["onboarding_step"] = step
        return _business_prompt(step)
    return _continue_onboarding(user)


def _process_profile_step(user: dict, message: str, field: str) -> dict:
    if not message:
        return _profile_prompt(field)
    if is_back_command(message) or message.lower() == PROFILE_BACK_ID:
        index = PROFILE_FIELDS.index(field)
        if index == 0:
            return _profile_prompt(field, "Esta es la primera pregunta.\n\n")
        previous = PROFILE_FIELDS[index - 1]
        user["profile_completion_step"] = previous
        return _profile_prompt(previous, "↩️ Puedes corregir este dato.\n\n")
    try:
        user[field] = PROFILE_VALIDATORS[field](message)
    except ProfileValidationError as error:
        return _profile_prompt(field, f"⚠️ {error}\n\n")
    return _continue_onboarding(user)


def _advance_onboarding(user: dict, message: str) -> dict:
    field = user.get("profile_completion_step")
    if field == "direccion_negocio":
        # Retomar sesiones de la versión anterior sin guardar la respuesta a
        # la dirección ni interpretarla como rubro, comuna u otro dato.
        user["profile_completion_step"] = "done"
        return _continue_onboarding(user, "La dirección ya no se solicita en el registro. Continuemos.\n\n")
    if field in PROFILE_FIELDS:
        return _process_profile_step(user, message, field)

    step = str(user.get("onboarding_step") or "0")
    if step not in {"1", "2", "3", "3_explicado"}:
        prefix = (
            "Vamos a completar los datos que faltan en tu perfil.\n\n"
            if step == "done" else
            "¡Hola! 👋 Soy *FinancIAl*. Te pediré tus datos personales y los de tu negocio "
            "para personalizar la atención. Tu celular se obtiene desde WhatsApp.\n\n"
        )
        return _continue_onboarding(user, prefix)

    if not message:
        return _business_prompt("3" if step == "3_explicado" else step)
    if is_back_command(message):
        if step == "1":
            if user.get("profile_completion_step") == "done":
                user["profile_completion_step"] = "email"
                return _profile_prompt("email")
            return _prompt_step_1("Esta es la primera pregunta del negocio.\n\n")
        previous = "1" if step == "2" else "2"
        user["onboarding_step"] = previous
        return _business_prompt(previous, "↩️ Puedes corregir tu respuesta anterior.\n\n")

    if step == "1":
        rubro = detect_rubro(message)
        if rubro is None:
            return _prompt_step_1("⚠️ Elige *Textil* o *Alimentos*, por ejemplo: vendo ropa o preparo comida.\n\n")
        user.update(rubro=rubro, rubro_raw=message.strip())
        return _continue_business(user)
    if step == "2":
        comuna = detect_comuna(message)
        if comuna is None:
            return _prompt_step_2("⚠️ Elige *Recoleta* o *El Bosque*, escribiendo la comuna o tocando un botón.\n\n")
        user["comuna"] = COMUNA_DISPLAY[comuna]
        return _continue_business(user)

    sii = detect_sii(message)
    if sii is None:
        return _prompt_step_3("⚠️ Responde *Sí*, *No* o *No sé*, o selecciona una opción de la lista.\n\n")
    if sii == "no_sabe" and step != "3_explicado":
        user["onboarding_step"] = "3_explicado"
        return _prompt_step_3(SII_EXPLANATION)
    user["inicio_sii"] = "no" if sii == "no_sabe" else sii
    return _continue_business(user)


def process_onboarding(user: dict, message: str, save_user_fn) -> dict:
    """Calcula el siguiente paso y confirma solo después de persistir sus cambios.

    save_user_fn recibe un parche parcial y debe devolver el registro guardado.
    Ante un error se conserva el estado original para permitir el reintento.
    """
    draft = copy.deepcopy(user)
    response = _advance_onboarding(draft, (message or "").strip())
    changes = {key: value for key, value in draft.items() if key not in user or user[key] != value}
    if not changes:
        return response
    try:
        saved = save_user_fn(user["phone"], changes)
        if not isinstance(saved, dict) or not saved:
            raise RuntimeError("No se confirmó el guardado del onboarding")
    except Exception as error:
        logger.error("No se pudo guardar el paso del onboarding (%s)", type(error).__name__)
        return {
            "type": "text",
            "body": "No pude guardar tu respuesta en este momento. Conservamos tu progreso anterior. Vuelve a enviar tu respuesta para intentarlo otra vez.",
        }
    user.update(changes)
    user.update(saved)
    return response
