import logging

logger = logging.getLogger("financial")

# Límite de caracteres de WhatsApp Cloud API para el body de un mensaje interactivo.
INTERACTIVE_BODY_LIMIT = 1024

# Botón para volver al Menú Principal de FinancIAl. Punto único de verdad
# reutilizado por core/ia.py, services/message_router.py, services/whatsapp.py
# y services/alertas_tributarias.py.
MENU_BUTTON_ID = "menu_financial"
MENU_BUTTON_LABEL = "📱 Menú principal"
MENU_BUTTON: list[tuple[str, str]] = [(MENU_BUTTON_ID, MENU_BUTTON_LABEL)]


def with_menu_button(widget: dict) -> dict:
    """Agrega el botón de Menú Principal a un widget {"type": "buttons"}.

    No modifica el widget si: no es de tipo "buttons", ya incluye el botón,
    o ya tiene 3 botones (límite de WhatsApp Cloud API) — en ese último caso
    solo registra un warning, nunca lanza excepción.
    """
    if widget.get("type") != "buttons":
        return widget
    options = list(widget.get("options", []))
    if any(option_id == MENU_BUTTON_ID for option_id, _ in options):
        return widget
    if len(options) >= 3:
        logger.warning(
            "No se pudo agregar el botón de menú: el widget ya tiene 3 botones (%s)",
            widget.get("body", "")[:60],
        )
        return widget
    return {**widget, "options": options + MENU_BUTTON}


MENU_OPTIONS_NO_FORMALIZADO = [
    ("menu_roadmap", "📋 Mi ruta de formalización"),
    ("menu_listo", "✅ Marcar paso listo"),
    ("menu_fondo", "🎯 Postular a fondos"),
    ("menu_recordatorios_on", "🔔 Activar recordatorios"),
    ("menu_recordatorios_off", "🔕 Pausar recordatorios"),
    ("menu_reiniciar", "🔄 Reiniciar"),
]

MENU_OPTIONS_FORMALIZADO = [
    ("menu_roadmap", "📈 Mi plan de crecimiento"),
    ("menu_fondo", "🎯 Postular a fondos"),
    ("menu_recordatorios_on", "🔔 Alertas SII (F29)"),
    ("menu_reiniciar", "🔄 Reiniciar"),
]


def get_menu_widget(user: dict | None = None, prefix: str = "") -> dict:
    """Retorna el menú interactivo con rubro y comuna según si el usuario está formalizado o no."""
    user = user or {}
    es_formalizado = user.get("inicio_sii") == "si"
    opciones = MENU_OPTIONS_FORMALIZADO if es_formalizado else MENU_OPTIONS_NO_FORMALIZADO

    rubro = user.get("rubro", user.get("rubro_raw", "tu negocio")).capitalize()
    comuna = user.get("comuna", "tu comuna")

    opciones_texto = "\n".join(f"• {label}" for _, label in opciones)
    titulo = "📈 *Panel de Crecimiento FinancIAl*" if es_formalizado else "📱 *Menú de FinancIAl*"

    body = (
        f"{prefix}"
        f"{titulo}\n"
        f"📍 _{rubro} · {comuna}_\n\n"
        "Opciones disponibles:\n\n"
        f"{opciones_texto}\n\n"
        "Tócalas en la lista de abajo o *escribe tu duda* para responderte con IA 🤖"
    )

    return {
        "type": "list",
        "body": body,
        "button_text": "Ver opciones",
        "options": opciones,
    }