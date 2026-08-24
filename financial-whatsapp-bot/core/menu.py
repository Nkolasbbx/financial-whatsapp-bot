MENU_OPTIONS_NO_FORMALIZADO = [
    ("menu_roadmap", "📋 Mi ruta de formalización"),
    ("menu_listo", "✅ Marcar paso listo"),
    ("menu_fondo", "🎯 Postular a fondos"),
    ("menu_recordatorios_on", "🔔 Activar recordatorios"),
    ("menu_reiniciar", "🔄 Reiniciar"),
]

MENU_OPTIONS_FORMALIZADO = [
    ("menu_roadmap", "📈 Mi plan de crecimiento"),
    ("menu_fondo", "🎯 Postular a fondos"),
    ("menu_recordatorios_on", "🔔 Alertas SII (F29)"),
    ("menu_reiniciar", "🔄 Reiniciar"),
]


def get_menu_widget(user: dict | None = None, prefix: str = "") -> dict:
    """Retorna el menú interactivo según si el usuario está formalizado o no."""
    es_formalizado = (user.get("inicio_sii") == "si") if user else False
    opciones = MENU_OPTIONS_FORMALIZADO if es_formalizado else MENU_OPTIONS_NO_FORMALIZADO

    opciones_texto = "\n".join(f"• {label}" for _, label in opciones)
    titulo = "📈 *Panel de Crecimiento FinancIAl*" if es_formalizado else "📱 *Menú de FinancIAl*"

    body = (
        f"{prefix}"
        f"{titulo}\n\n"
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