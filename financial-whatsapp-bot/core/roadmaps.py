import copy
from datetime import datetime

from core.menu import get_menu_widget

ROADMAPS = {
    "textil": [
        {"id": 1, "title": "Obtener Cédula de Identidad vigente", "desc": "Necesitas tu CI vigente para todos los trámites. Si está vencida, renuévala en el Registro Civil.", "done": False},
        {"id": 2, "title": "Obtener RUT en el SII", "desc": "Si no tienes RUT, inscríbete en sii.cl o en oficina del SII. Es gratis y en el día.", "done": False},
        {"id": 3, "title": "Inicio de Actividades en el SII", "desc": "Entra a sii.cl → 'Inicio de actividades'. Elige la categoría textil/confección.", "done": False},
        {"id": 4, "title": "Solicitar Patente Municipal", "desc": "Ve a tu municipalidad con el inicio de actividades y solicita la patente comercial.", "done": False},
        {"id": 5, "title": "Resolución Sanitaria (si aplica)", "desc": "Si trabajas con telas que requieren tratamiento especial, podrías necesitar resolución SEREMI.", "done": False},
        {"id": 6, "title": "Emitir tu primera boleta", "desc": "¡Ya puedes facturar! Entra al SII y emite boletas electrónicas.", "done": False},
    ],
    "alimentos": [
        {"id": 1, "title": "Obtener Cédula de Identidad vigente", "desc": "Tu CI vigente es necesaria para todo el proceso.", "done": False},
        {"id": 2, "title": "Obtener RUT en el SII", "desc": "Inscríbete en sii.cl si no tienes RUT. Gratis y en el día.", "done": False},
        {"id": 3, "title": "Inicio de Actividades en el SII", "desc": "Entra a sii.cl y selecciona la categoría de alimentos.", "done": False},
        {"id": 4, "title": "Resolución Sanitaria SEREMI", "desc": "OBLIGATORIO para alimentos. Solicita autorización en la SEREMI de Salud. Necesitas informe de condiciones de tu cocina/taller.", "done": False},
        {"id": 5, "title": "Autorización SAG (si aplica)", "desc": "Si vendes productos de origen animal (snacks mascotas, lácteos, etc.), necesitas permiso del SAG.", "done": False},
        {"id": 6, "title": "Solicitar Patente Municipal", "desc": "Con inicio de actividades y resolución sanitaria, solicita la patente en tu municipalidad.", "done": False},
        {"id": 7, "title": "Emitir tu primera boleta", "desc": "¡Listo! Ya puedes emitir boletas electrónicas desde el SII.", "done": False},
    ],
    "otro": [
        {"id": 1, "title": "Obtener Cédula de Identidad vigente", "desc": "Tu CI vigente es el primer paso.", "done": False},
        {"id": 2, "title": "Obtener RUT en el SII", "desc": "Inscríbete en sii.cl.", "done": False},
        {"id": 3, "title": "Inicio de Actividades en el SII", "desc": "Entra a sii.cl → 'Inicio de actividades' y selecciona tu categoría.", "done": False},
        {"id": 4, "title": "Verificar permisos sectoriales", "desc": "Dependiendo de tu rubro, podrías necesitar permisos adicionales. Pregúntame y te oriento.", "done": False},
        {"id": 5, "title": "Solicitar Patente Municipal", "desc": "Ve a tu municipalidad con el inicio de actividades.", "done": False},
        {"id": 6, "title": "Emitir tu primera boleta", "desc": "¡Ya puedes facturar!", "done": False},
    ],
}

HITO_LISTO_ID = "hito_listo"
HITO_AYUDA_ID = "hito_ayuda"
HITO_VOLVER_ID = "hito_volver"
MENU_FINANCIAL_ID = "menu_financial"
MENU_FONDO_ID = "menu_fondo"
MENU_ROADMAP_ID = "menu_roadmap"
MENU_RECORDATORIOS_ID = "menu_recordatorios_on"

HITO_BUTTON_OPTIONS = [
    (HITO_LISTO_ID, "✅ Listo"),
    (HITO_AYUDA_ID, "❓ Ayuda"),
    (MENU_FINANCIAL_ID, "📱 Menú"),
]


def _buttons(body: str, options: list[tuple[str, str]]) -> dict:
    return {"type": "buttons", "body": body, "options": options}


def get_pending_milestone(user: dict) -> dict | None:
    return next(
        (hito for hito in user.get("roadmap", []) if not hito.get("done")),
        None,
    )


def get_last_completed_milestone(user: dict) -> dict | None:
    roadmap = user.get("roadmap", [])
    completed = [h for h in roadmap if h.get("done")]
    return completed[-1] if completed else None


def _progress_bar(user: dict) -> tuple[str, int, int, int]:
    roadmap = user.get("roadmap", [])
    completed = sum(1 for h in roadmap if h.get("done"))
    total = len(roadmap)
    pct = round((completed / total) * 100) if total else 0
    filled = round(pct / 10)
    bar = "🟩" * filled + "⬜" * (10 - filled)
    return bar, pct, completed, total


def get_roadmap_text(user: dict) -> dict:
    es_formalizado = user.get("inicio_sii") == "si"

    # ── CASO 1: FORMALIZADO (Plan de crecimiento sin trámites) ──
    if es_formalizado:
        rubro = user.get("rubro", "tu negocio").capitalize()
        comuna = user.get("comuna", "tu comuna")

        body = (
            f"📈 *Plan de Crecimiento FinancIAl*\n"
            f"📍 _{rubro} · {comuna} (Formalizado ante SII)_\n\n"
            "Tu negocio ya opera legalmente. Aquí están tus focos estratégicos:\n\n"
            "• 🎯 *Fondos Concursables:* Postulaciones a Sercotec (Crece, Abeja) y Corfo.\n"
            "• 🔔 *Obligaciones Tributarias:* Alertas para declaración mensual de IVA (F29).\n"
            "• 📊 *Optimización:* Análisis de costos, precios y márgenes de ganancia.\n\n"
            "¿Qué área deseas gestionar hoy?"
        )

        return _buttons(
            body,
            [
                (MENU_FONDO_ID, "🎯 Ver fondos"),
                (MENU_FINANCIAL_ID, "📱 Menú principal"),
            ],
        )

    # ── CASO 2: NO FORMALIZADO (Ruta secuencial) ──
    roadmap = user.get("roadmap", [])
    if not roadmap:
        return {
            "type": "text",
            "body": "⚠️ No tienes una ruta de formalización activa. Escribe *hola* para empezar.",
        }

    bar, pct, completed, total = _progress_bar(user)

    lines = [
        f"📋 *Tu Ruta de Formalización*\n",
        f"{bar} {pct}%",
        f"_{completed} de {total} trámites completados_\n",
    ]

    for h in roadmap:
        status = "✅" if h.get("done") else "⬜"
        lines.append(f"{status} *{h['title']}*")
        if not h.get("done"):
            lines.append(f"   ↳ _{h['desc']}_\n")

    next_hito = get_pending_milestone(user)

    if next_hito:
        lines.append(f"\n👉 *Paso pendiente:* {next_hito['title']}")
        lines.append("\n_Escribe 'deshacer' si necesitas retroceder un paso._")
        body = "\n".join(lines)
        return _buttons(body, HITO_BUTTON_OPTIONS)

    felicitacion = "🎉 *¡Completaste todos los trámites de formalización!* 🏢\n\n"
    return get_menu_widget(user, prefix=felicitacion)


def revert_last_hito(user: dict, save_user_fn) -> dict:
    if user.get("inicio_sii") == "si":
        return get_roadmap_text(user)

    last_completed = get_last_completed_milestone(user)

    if not last_completed:
        return {
            "type": "text",
            "body": "No hay ningún hito completado que deshacer todavía.",
        }

    last_completed["done"] = False
    save_user_fn(user["phone"], user)

    _, pct, completed, total = _progress_bar(user)

    body = (
        f"↩️ Volviste a dejar pendiente: *{last_completed['title']}*\n\n"
        f"📊 Progreso: {pct}% ({completed}/{total})\n\n"
        f"_{last_completed['desc']}_"
    )
    return _buttons(body, HITO_BUTTON_OPTIONS)


def extract_hito_context(user: dict) -> dict | None:
    hito = get_pending_milestone(user)
    if not hito:
        return None

    return {
        "title": hito.get("title", ""),
        "description": hito.get("desc", ""),
        "rubro": user.get("rubro", "No definido"),
        "comuna": user.get("comuna", "No definida"),
    }


def mark_hito_done(user: dict, save_user_fn) -> dict:
    if user.get("inicio_sii") == "si":
        return get_roadmap_text(user)

    current = get_pending_milestone(user)

    if not current:
        return get_menu_widget(user, prefix="🎉 ¡Ya completaste todos los trámites!\n\n")

    current["done"] = True
    save_user_fn(user["phone"], user)

    _, pct, completed, total = _progress_bar(user)
    next_hito = get_pending_milestone(user)

    if next_hito:
        body = (
            f"✅ ¡Bien! Completaste: *{current['title']}*\n\n"
            f"📊 Progreso: {pct}% ({completed}/{total})\n\n"
            f"👉 *Siguiente trámite:*\n"
            f"*{next_hito['title']}*\n"
            f"_{next_hito['desc']}_\n\n"
            f"_Escribe 'deshacer' si necesitas corregir este paso._"
        )
        return _buttons(body, HITO_BUTTON_OPTIONS)

    # 100% completado -> migra a formalizado
    user["inicio_sii"] = "si"
    user["roadmap"] = []
    user["roadmap_completed_at"] = datetime.utcnow().isoformat()
    user["roadmap_completion_stats"] = {
        "total_hitos": total,
        "rubro": user.get("rubro", "No definido"),
        "comuna": user.get("comuna", "No definida"),
    }
    save_user_fn(user["phone"], user)

    rubro_display = user.get("rubro", "tu emprendimiento").capitalize()
    comuna_display = user.get("comuna", "tu zona")

    felicitacion = (
        f"✅ *¡Completaste: {current['title']}!*\n\n"
        f"🎉 🎉 🎉 *¡¡FELICITACIONES!!* 🎉 🎉 🎉\n\n"
        f"Acabas de completar el *100%* de tu formalización.\n\n"
        f"📈 *Tu logro:*\n"
        f"• _{total} trámites completados_\n"
        f"• _Rubro: {rubro_display}_\n"
        f"• _Comuna: {comuna_display}_\n\n"
        f"¡Tu negocio está *oficialmente formalizado*! 🏢\n\n"
    )

    return get_menu_widget(user, prefix=felicitacion)