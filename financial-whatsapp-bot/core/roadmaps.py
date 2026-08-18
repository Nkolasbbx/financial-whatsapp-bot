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

# ids usados en los botones interactivos de Meta relacionados al roadmap.
# Se exportan para que message_router.py los reconozca como equivalentes
# a sus comandos de texto (listo, ayuda, deshacer).
HITO_LISTO_ID = "hito_listo"
HITO_AYUDA_ID = "hito_ayuda"
HITO_VOLVER_ID = "hito_volver"
FONDO_ID = "menu_fondo"


def _buttons(body: str, options: list[tuple[str, str]]) -> dict:
    return {"type": "buttons", "body": body, "options": options}


def get_pending_milestone(user: dict) -> dict | None:
    """Devuelve el primer hito pendiente del roadmap."""
    return next(
        (hito for hito in user.get("roadmap", []) if not hito.get("done")),
        None,
    )


def get_last_completed_milestone(user: dict) -> dict | None:
    """Devuelve el último hito marcado como completado (el más reciente
    en orden), o None si no hay ninguno completado."""
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
    """Genera el mensaje de estado del roadmap, con botones de acción."""
    roadmap = user.get("roadmap", [])
    if not roadmap:
        return {
            "type": "text",
            "body": "⚠️ No tienes un roadmap generado. Escribe *hola* para empezar.",
        }

    bar, pct, completed, total = _progress_bar(user)

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
    has_completed = get_last_completed_milestone(user) is not None

    if next_hito:
        lines.append(f"\n👉 *Tu siguiente paso:* {next_hito['title']}")
        body = "\n".join(lines)

        options = [(HITO_LISTO_ID, "✅ Listo"), (HITO_AYUDA_ID, "❓ Ayuda")]
        if has_completed:
            options.append((HITO_VOLVER_ID, "↩️ Deshacer paso"))
        return _buttons(body, options)

    lines.append("\n🎉 *¡Completaste todos los hitos!* Tu negocio está formalizado.")
    body = "\n".join(lines)

    options = [(FONDO_ID, "🎯 Postular a fondo")]
    if has_completed:
        options.append((HITO_VOLVER_ID, "↩️ Deshacer paso"))
    return _buttons(body, options)

def revert_last_hito(user: dict, save_user_fn) -> dict:
    """Deshace el último hito marcado como completado, volviendo a
    dejarlo pendiente. Permite corregir un "listo" enviado por error."""
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
    return _buttons(
        body,
        [(HITO_LISTO_ID, "✅ Listo"), (HITO_AYUDA_ID, "❓ Ayuda")],
    )

def extract_hito_context(user: dict) -> dict | None:
    """
    Extrae el contexto del hito pendiente para inyectar en la IA.
    
    Se usa cuando el usuario solicita "Ayuda" sobre un hito específico.
    La IA recibirá este contexto en su system prompt para dar respuestas
    muy específicas y contextualizadas.
    
    Args:
        user: dict con datos del usuario
        
    Returns:
        dict con keys {title, description, rubro, comuna} o None si no hay hito pendiente
    """
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
    """
    Versión mejorada de mark_hito_done() que genera mensajes épicos
    cuando se completa el último hito (roadmap 100%).
    
    CAMBIOS RESPECTO A LA VERSION ORIGINAL:
    - Cuando completa el ÚLTIMO hito: mensaje celebratorio épico
    - Incluye resumen: cantidad de hitos, rubro, comuna
    - Destaca "Postular a fondo" como siguiente acción principal
    - Guarda un evento de "roadmap_completed" en el usuario (opcional, para analytics)
    
    Args:
        user: dict con datos del usuario
        save_user_fn: función para guardar usuario
        
    Returns:
        dict {"type": "buttons" | "text", "body": ..., "options": ...}
    """
    roadmap = user.get("roadmap", [])
    current = get_pending_milestone(user)

    if not current:
        return {
            "type": "text",
            "body": "🎉 ¡Ya completaste todos los hitos! No hay más pendientes.",
        }

    current["done"] = True
    save_user_fn(user["phone"], user)

    _, pct, completed, total = _progress_bar(user)
    next_hito = get_pending_milestone(user)

    # ── CASO: Aún hay más hitos por completar ──
    if next_hito:
        body = (
            f"✅ ¡Bien! Completaste: *{current['title']}*\n\n"
            f"📊 Progreso: {pct}% ({completed}/{total})\n\n"
            f"👉 *Tu siguiente paso:*\n"
            f"*{next_hito['title']}*\n"
            f"_{next_hito['desc']}_"
        )
        return _buttons(
            body,
            [
                (HITO_LISTO_ID, "✅ Listo"),
                (HITO_AYUDA_ID, "❓ Ayuda"),
                (HITO_VOLVER_ID, "↩️ Deshacer paso"),
            ],
        )

    # ── CASO: ES EL ÚLTIMO HITO - MENSAJE CELEBRATORIO ÉPICO ──
    
    # Registro del evento (opcional, para analytics)
    user["roadmap_completed_at"] = __import__("datetime").datetime.utcnow().isoformat()
    user["roadmap_completion_stats"] = {
        "total_hitos": total,
        "rubro": user.get("rubro", "No definido"),
        "comuna": user.get("comuna", "No definida"),
    }
    save_user_fn(user["phone"], user)
    
    # Mensaje celebratorio épico
    rubro_display = user.get("rubro", "tu emprendimiento").capitalize()
    comuna_display = user.get("comuna", "tu zona")
    
    body = (
        f"✅ *¡Completaste: {current['title']}!*\n\n"
        f"🎉 🎉 🎉 *¡¡FELICITACIONES!!* 🎉 🎉 🎉\n\n"
        f"Acabas de completar el *100%* de tu roadmap de formalización.\n\n"
        f"📈 *Tu logro:*\n"
        f"• _{total} trámites completados_\n"
        f"• _Rubro: {rubro_display}_\n"
        f"• _Comuna: {comuna_display}_\n\n"
        f"¡Tu negocio está *oficialmente formalizado*! 🏢\n\n"
        f"💪 Ahora es momento de hacerlo crecer. "
        f"Tenemos *fondos concursables* que podrían ayudarte."
    )
    
    return _buttons(
        body,
        [
            (FONDO_ID, "🎯 Ver fondos disponibles"),
            (HITO_VOLVER_ID, "↩️ Deshacer paso"),
        ],
    )
