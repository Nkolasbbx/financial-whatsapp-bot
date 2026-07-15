from db.users import get_user, save_user
from core.roadmaps import get_roadmap_text, mark_hito_done
from core.fondos import simulate_funds
from core.onboarding import process_onboarding


def route_message(phone: str, message: str) -> str:
    """Main router: determines what to do with each incoming message."""
    message = message.strip()
    msg_lower = message.lower()

    # Get or create user
    user = get_user(phone)

    if not user:
        user = {"phone": phone, "onboarding_step": 0}
        save_user(phone, user)

    # ── Onboarding flow ──
    if user.get("onboarding_step") != "done":
        response = process_onboarding(user, message, save_user)
        if response:
            return response

    # ── Reset command ──
    if msg_lower in ["reiniciar", "reset", "empezar de nuevo"]:
        from db.users import users_db
        users_db.pop(phone, None)
        new_user = {"phone": phone, "onboarding_step": 0}
        save_user(phone, new_user)
        return process_onboarding(new_user, message, save_user)

    # ── Roadmap commands ──
    roadmap_triggers = ["roadmap", "mi roadmap", "hitos", "qué me falta", "que me falta", "formalizar", "mis pasos", "mi ruta"]
    if any(trigger in msg_lower for trigger in roadmap_triggers):
        return get_roadmap_text(user)

    # ── Mark hito done ──
    done_triggers = ["listo", "hecho", "completado", "ya lo hice", "ya está", "ya esta", "siguiente"]
    if any(trigger in msg_lower for trigger in done_triggers):
        return mark_hito_done(user, save_user)

    # ── Fund simulation ──
    fund_triggers = ["fondo", "postular", "capital semilla", "capital abeja", "sercotec", "corfo", "financiamiento"]
    if any(trigger in msg_lower for trigger in fund_triggers):
        return simulate_funds(user)

    # ── Help ──
    if msg_lower in ["ayuda", "help", "menu", "menú", "opciones"]:
        return (
            "📱 *Menú de FinancIAl*\n\n"
            "Escribe cualquiera de estas opciones:\n\n"
            "📋 *\"mi roadmap\"* → ver tu progreso de formalización\n"
            "✅ *\"listo\"* → marcar el hito actual como completado\n"
            "🎯 *\"postular a fondo\"* → simular postulación a fondos\n"
            "❓ *\"ayuda\"* → ver este menú\n"
            "🔄 *\"reiniciar\"* → empezar de nuevo\n\n"
            "💬 O simplemente *escribe tu pregunta* y te respondo con IA 🤖"
        )

    # ── AI Chat (default) ──
    return "__AI_QUERY__"


def split_message(text: str, max_len: int) -> list[str]:
    """Split long message into chunks at paragraph boundaries."""
    if len(text) <= max_len:
        return [text]

    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break

        # Find last newline before limit
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len

        parts.append(text[:split_at])
        text = text[split_at:].lstrip("\n")

    return parts