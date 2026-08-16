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
    "joyeria": [
        {"id": 1, "title": "Obtener Cédula de Identidad vigente", "desc": "Tu CI vigente es necesaria para todo el proceso.", "done": False},
        {"id": 2, "title": "Obtener RUT en el SII", "desc": "Inscríbete en sii.cl si no tienes RUT.", "done": False},
        {"id": 3, "title": "Inicio de Actividades en el SII", "desc": "Entra a sii.cl y elige la categoría artesanía/joyería.", "done": False},
        {"id": 4, "title": "Solicitar Patente Municipal", "desc": "Ve a la municipalidad con tu inicio de actividades.", "done": False},
        {"id": 5, "title": "Emitir tu primera boleta", "desc": "¡Ya puedes facturar oficialmente!", "done": False},
    ],
    "otro": [
        {"id": 1, "title": "Obtener Cédula de Identidad vigente", "desc": "Tu CI vigente es el primer paso.", "done": False},
        {"id": 2, "title": "Obtener RUT en el SII", "desc": "Inscríbete en sii.cl.", "done": False},
        {"id": 3, "title": "Inicio de Actividades en el SII", "desc": "Entra a sii.cl → 'Inicio de actividades' y selecciona tu categoría.", "done": False},
        {"id": 4, "title": "Verificar permisos sectoriales", "desc": "Dependiendo de tu rubro, podrías necesitar permisos adicionales. Pregúntame y te oriento.", "done": False},
        {"id": 5, "title": "Solicitar Patente Municipal", "desc": "Ve a tu municipalidad con el inicio de actividades.", "done": False},
        {"id": 6, "title": "Emitir tu primera boleta", "desc": "¡Ya puedes facturar!", "done": False},
    ],
    "formalizado": [
        {"id": 1, "title": "✅ Ya estás formalizado", "desc": "Tu negocio ya tiene inicio de actividades. Ahora enfócate en crecer.", "done": True},
        {"id": 2, "title": "Revisar obligaciones tributarias", "desc": "Verifica que estés al día con declaraciones mensuales (F29) y anuales.", "done": False},
        {"id": 3, "title": "Explorar fondos concursables", "desc": "Revisa si calificas para Capital Semilla, Capital Abeja, CORFO u otros.", "done": False},
        {"id": 4, "title": "Optimizar tu negocio", "desc": "Pregúntame sobre métricas, precios, costos o estrategias para tu rubro.", "done": False},
    ],
}


def get_pending_milestone(user: dict) -> dict | None:
    """Devuelve el primer hito pendiente del roadmap."""
    return next(
        (hito for hito in user.get("roadmap", []) if not hito.get("done")),
        None,
    )


def get_roadmap_text(user: dict) -> str:
    """Generate roadmap status message."""
    roadmap = user.get("roadmap", [])
    if not roadmap:
        return "⚠️ No tienes un roadmap generado. Escribe *hola* para empezar."

    completed = sum(1 for h in roadmap if h.get("done"))
    total = len(roadmap)
    pct = round((completed / total) * 100)

    # Progress bar
    filled = round(pct / 10)
    bar = "🟩" * filled + "⬜" * (10 - filled)

    lines = [
        f"📋 *Tu Roadmap de Formalización*\n",
        f"{bar} {pct}%",
        f"_{completed} de {total} hitos completados_\n",
    ]

    for h in roadmap:
        status = "✅" if h["done"] else "⬜"
        lines.append(f"{status} *{h['title']}*")
        if not h["done"]:
            lines.append(f"   ↳ _{h['desc']}_\n")

    next_hito = get_pending_milestone(user)
    if next_hito:
        lines.append(f"\n👉 *Tu siguiente paso:* {next_hito['title']}")
        lines.append(f"\nEscribe *\"listo\"* cuando completes este hito, o *\"ayuda\"* si necesitas orientación.")
    else:
        lines.append("\n🎉 *¡Completaste todos los hitos!* Tu negocio está formalizado.")
        lines.append("\nEscribe *\"postular a fondo\"* para explorar financiamiento.")

    return "\n".join(lines)


def mark_hito_done(user: dict, save_user_fn) -> str:
    """Mark current hito as done and show next."""
    roadmap = user.get("roadmap", [])
    current = get_pending_milestone(user)

    if not current:
        return "🎉 ¡Ya completaste todos los hitos! No hay más pendientes."

    current["done"] = True
    save_user_fn(user["phone"], user)

    completed = sum(1 for h in roadmap if h["done"])
    total = len(roadmap)
    pct = round((completed / total) * 100)

    next_hito = get_pending_milestone(user)

    if next_hito:
        return (
            f"✅ ¡Bien! Completaste: *{current['title']}*\n\n"
            f"📊 Progreso: {pct}% ({completed}/{total})\n\n"
            f"👉 *Tu siguiente paso:*\n"
            f"*{next_hito['title']}*\n"
            f"_{next_hito['desc']}_\n\n"
            f"Escribe *\"listo\"* al completarlo, o *\"ayuda\"* si necesitas orientación."
        )
    else:
        return (
            f"✅ ¡Completaste: *{current['title']}*\n\n"
            f"🎉🎉🎉 *¡FELICITACIONES!* 🎉🎉🎉\n\n"
            f"Completaste el 100% de tu roadmap. ¡Tu negocio está formalizado!\n\n"
            f"¿Qué sigue?\n"
            f"🎯 Escribe *\"postular a fondo\"* para buscar financiamiento\n"
            f"💬 O hazme cualquier pregunta sobre cómo hacer crecer tu negocio"
        )
