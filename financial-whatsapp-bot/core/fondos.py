"""
FinancIAl — core/fondos.py
Simulador conversacional de postulación a fondos concursables.

Cubre los criterios de aceptación de HdU05:
- CA1 [PMV] Muestra checklist con requisitos cumplidos/no cumplidos y % de compatibilidad.
- CA2 [PMV] Muestra fondos según perfil del usuario.
- CA3       Junto a cada ❌ incluye recomendación concreta y plazo estimado.
- CA4       Lee fechas desde Supabase, informa cierre y prioriza fondos vigentes.
- CA5       Cada simulación lee el perfil actualizado del usuario (sin caché).
- CA-urgencia (HdU05 backlog Sprint 1, "Evaluación de elegibilidad"): ordena
  los requisitos pendientes de mayor a menor urgencia según su plazo
  estimado (`plazo_dias`) vs. los días restantes del cierre, mostrando
  primero los que todavía son alcanzables a tiempo.
"""

from datetime import date, datetime
import logging

from db.fondos import (
    get_fund_answers,
    get_requirement_definitions,
    list_active_funds,
)

logger = logging.getLogger("financial")


def _evaluar_requisito_legacy(clave: str, user: dict) -> bool | None:
    """Evaluación de respaldo para instalaciones sin el catálogo nuevo."""
    is_formal = user.get("inicio_sii") == "si"

    rubro_lower = (user.get("rubro_raw") or user.get("rubro", "")).lower()
    rubros_pioneras = [
        "construccion", "construcción",
        "tecnologia", "tecnología", "informatica", "informática", "computador",
        "transporte", "almacenamiento",
        "mineria", "minería",
        "electricidad", "gas", "vapor",
        "agua", "residuos", "descontaminacion",
        "vehiculo", "vehículo", "automotor", "motocicleta",
        "manufactura industrial",
    ]
    rubros_excluidos = [
        "textil", "alimento", "joyeria", "joyería",
        "artesania", "artesanía", "costura", "confeccion", "confección"
    ]

    es_rubro_excluido = any(r in rubro_lower for r in rubros_excluidos)
    es_rubro_pioneras = (
        any(r in rubro_lower for r in rubros_pioneras) and not es_rubro_excluido
    )
    if not es_rubro_excluido and not es_rubro_pioneras:
        es_rubro_pioneras_result = None
    elif es_rubro_excluido:
        es_rubro_pioneras_result = False
    else:
        es_rubro_pioneras_result = True

    evaluadores = {
        "mayor_edad":             True,
        "sin_inicio_sii":         not is_formal,
        "inicio_sii":             is_formal,
        "inicio_sii_12m":         False if not is_formal else None,
        "ventas_semilla":         None,
        "ventas_crece":           None,
        "capacitacion":           False,
        "capacitacion_crece":     False,
        "genero_femenino":        None,
        "sin_beneficio_reciente": None,
        "patente":                is_formal,
        "proyecto_negocio":       None,
        "actividad_coherente":    None,
        "rubro_pioneras":         es_rubro_pioneras_result,
    }

    return evaluadores.get(clave, None)


def _evaluate_custom_requirement(
    handler: str | None,
    user: dict,
) -> bool | None:
    """Ejecuta reglas calculadas que no se expresan con un operador simple."""
    if handler != "rubro_pioneras":
        return None

    rubro_lower = (user.get("rubro_raw") or user.get("rubro", "")).lower()
    rubros_pioneras = [
        "construccion", "construcción",
        "tecnologia", "tecnología", "informatica", "informática", "computador",
        "transporte", "almacenamiento",
        "mineria", "minería",
        "electricidad", "gas", "vapor",
        "agua", "residuos", "descontaminacion",
        "vehiculo", "vehículo", "automotor", "motocicleta",
        "manufactura industrial",
    ]
    rubros_excluidos = [
        "textil", "alimento", "joyeria", "joyería",
        "artesania", "artesanía", "costura", "confeccion", "confección",
    ]

    if any(rubro in rubro_lower for rubro in rubros_excluidos):
        return False
    if any(rubro in rubro_lower for rubro in rubros_pioneras):
        return True
    return None


def _apply_evaluation_rule(value, rule: dict, user: dict) -> bool | None:
    """Aplica una regla declarativa del catálogo de requisitos."""
    operator = rule.get("operator")
    if operator == "custom":
        return _evaluate_custom_requirement(rule.get("handler"), user)

    if value is None:
        return None

    if operator == "equals":
        return value == rule.get("expected")

    if operator == "between":
        if isinstance(value, bool):
            return None
        try:
            numeric_value = float(value)
            minimum = float(rule["min"])
            maximum = float(rule["max"])
        except (KeyError, TypeError, ValueError):
            return None
        return minimum <= numeric_value <= maximum

    return None


def evaluate_requirement(
    requirement: dict,
    user: dict,
    answers: dict,
    definitions: dict[str, dict],
) -> bool | None:
    """Evalúa un requisito con datos del perfil o respuestas persistidas."""
    field_key = requirement.get("clave", "")
    definition = definitions.get(field_key)
    if not definition:
        return _evaluar_requisito_legacy(field_key, user)

    source_type = definition.get("source_type")
    if source_type == "user_profile":
        value = user.get(definition.get("profile_field"))
    elif source_type == "user_answer":
        value = answers.get(field_key)
    elif source_type == "computed":
        value = None
    else:
        return None

    return _apply_evaluation_rule(
        value,
        definition.get("evaluation_rule") or {},
        user,
    )


def _with_dynamic_recommendation(
    requirement: dict,
    result: bool | None,
    answers: dict,
) -> dict:
    """Ajusta recomendaciones que dependen del valor respondido."""
    enriched = dict(requirement)
    if requirement.get("clave") != "ventas_crece" or result is not False:
        return enriched

    value = answers.get("ventas_crece")
    try:
        sales = float(value)
    except (TypeError, ValueError):
        return enriched

    if sales < 200:
        enriched.update({
            "corregible": True,
            "recomendacion": (
                "Tus ventas todavía están bajo el mínimo de 200 UF. "
                "Prepara un plan comercial para aumentar ventas y revisa "
                "fondos dirigidos a negocios de menor tamaño mientras alcanzas "
                "el requisito."
            ),
        })
    elif sales > 25000:
        enriched.update({
            "corregible": False,
            "recomendacion": (
                "Tus ventas superan el máximo de 25.000 UF para Crece. "
                "Busca instrumentos destinados a empresas de mayor tamaño."
            ),
        })
    return enriched


def evaluate_fund(
    fund: dict,
    user: dict,
    answers: dict,
    definitions: dict[str, dict],
    today: date | None = None,
    answered_keys: set[str] | None = None,
) -> dict:
    """Produce un resultado estructurado y reutilizable para un fondo."""
    current_date = today or date.today()
    closing_date = fund.get("fecha_cierre")
    if isinstance(closing_date, str):
        try:
            closing_date = datetime.strptime(closing_date, "%Y-%m-%d").date()
        except ValueError:
            closing_date = None

    days_remaining = (
        (closing_date - current_date).days
        if isinstance(closing_date, date)
        else None
    )

    evaluated_requirements = []
    for position, requirement in enumerate(fund.get("requisitos") or []):
        field_key = requirement.get("clave", "")
        definition = definitions.get(field_key, {})
        result = evaluate_requirement(requirement, user, answers, definitions)
        requirement = _with_dynamic_recommendation(
            requirement,
            result,
            answers,
        )
        evaluated_requirements.append({
            **requirement,
            "cumple": result,
            "question": definition.get("question"),
            "answer_type": definition.get("answer_type"),
            "options": definition.get("options") or [],
            "evaluation_rule": definition.get("evaluation_rule") or {},
            "question_order": definition.get("question_order", 100),
            "position": position,
        })

    total = len(evaluated_requirements)
    met = sum(req["cumple"] is True for req in evaluated_requirements)
    failed = sum(req["cumple"] is False for req in evaluated_requirements)
    unknown = total - met - failed

    blocking_failures = [
        req
        for req in evaluated_requirements
        if req["cumple"] is False
        and req.get("obligatorio", True)
        and req.get("corregible") is False
    ]
    missing_questions = sorted(
        (
            req
            for req in evaluated_requirements
            if req["cumple"] is None
            and req.get("question")
            and req.get("clave") not in (answered_keys or set())
        ),
        key=lambda req: (req["question_order"], req["position"]),
    )

    return {
        "fund": {**fund, "fecha_cierre": closing_date},
        "requirements": evaluated_requirements,
        "met": met,
        "failed": failed,
        "unknown": unknown,
        "total": total,
        "percentage": round((met / total) * 100) if total else 0,
        "days_remaining": days_remaining,
        "is_open": days_remaining is not None and days_remaining >= 0,
        "blocking_failures": blocking_failures,
        "missing_questions": missing_questions,
    }


def fund_applies_to_user(fund: dict, user: dict) -> bool:
    """Indica si el fondo corresponde al estado de formalización del perfil."""
    return _fondo_aplica_para_usuario(
        fund,
        user.get("inicio_sii") == "si",
    )


def _get_mensaje_requisito(req: dict, cumple: bool | None, is_formal: bool) -> list[str]:
    """
    Genera las líneas de texto para un requisito según su estado.
    Manejo especial para sin_inicio_sii cuando el usuario ya está formalizado.
    """
    lines = []
    clave = req.get("clave", "")

    if cumple is True:
        lines.append(f"  ✅ {req['texto']}")

    elif cumple is False:
        # Caso especial: usuario formalizado en fondo que requiere NO tener inicio SII
        if clave == "sin_inicio_sii" and is_formal:
            lines.append(f"  ℹ️ {req['texto']}")
            lines.append(
                "     💡 _Ya estás formalizado, por lo que no puedes postular a este fondo. "
                "¡Buenas noticias! Puedes postular a *Crece* de SERCOTEC, "
                "que está diseñado para emprendedores ya formalizados._"
            )
        else:
            # CA3: recomendación concreta + plazo
            lines.append(f"  ❌ {req['texto']}")
            if req.get("recomendacion"):
                lines.append(f"     💡 _{req['recomendacion']}_")
            if req.get("plazo"):
                lines.append(f"     ⏱ _Tiempo estimado: {req['plazo']}_")

    else:
        # None: necesita más info
        lines.append(f"  ⚠️ {req['texto']} _(necesito más info)_")
        if req.get("recomendacion"):
            lines.append(f"     💡 _{req['recomendacion']}_")

    return lines


FONDOS_FALLBACK = [
    {
        "nombre": "Capital Semilla Emprende",
        "emoji": "💰",
        "link": "https://www.sercotec.cl/programas/capital-semilla-emprende/",
        "monto_max": 3500000,
        "fecha_cierre": date(2027, 4, 30),
        "activo": True,
        "requisitos": [
            {"texto": "Persona natural mayor de 18 años", "clave": "mayor_edad", "recomendacion": "Este fondo exige ser mayor de 18 años. Mientras tanto, puedes avanzar en capacitaciones y preparar tu proyecto.", "plazo": None},
            {"texto": "Sin inicio de actividades en el SII", "clave": "sin_inicio_sii", "recomendacion": "Si ya tienes inicio de actividades, revisa el fondo Crece para negocios formalizados.", "plazo": None},
            {"texto": "Ventas menores a 2.400 UF/año (~$90M CLP)", "clave": "ventas_semilla", "recomendacion": "Cuéntame cuánto vendiste el último año.", "plazo": None},
            {"texto": "Capacitación en gestión empresarial", "clave": "capacitacion", "recomendacion": "Inscríbete en capacitasercotec.cl o en InnovaRecoleta. Ofrecen talleres gratuitos.", "plazo": "2 a 4 semanas", "plazo_dias": 28},
        ],
    },
    {
        "nombre": "Capital Abeja Emprende",
        "emoji": "🐝",
        "link": "https://www.sercotec.cl/programas/capital-abeja-emprende/",
        "monto_max": 3500000,
        "fecha_cierre": date(2027, 4, 30),
        "activo": True,
        "requisitos": [
            {"texto": "Mujer emprendedora (sexo registral femenino)", "clave": "genero_femenino", "recomendacion": "Puedes revisar Capital Semilla Emprende u otros fondos sin este requisito.", "plazo": None},
            {"texto": "Mayor de 18 años", "clave": "mayor_edad", "recomendacion": "Mientras tanto, puedes avanzar en capacitaciones y preparar tu proyecto.", "plazo": None},
            {"texto": "Sin inicio de actividades en primera categoría SII", "clave": "sin_inicio_sii", "recomendacion": "Si ya estás formalizado, revisa el fondo Crece.", "plazo": None},
            {"texto": "Sin beneficios SERCOTEC en los últimos 2 años", "clave": "sin_beneficio_reciente", "recomendacion": "Verifica la fecha de tu último beneficio o revisa otras convocatorias.", "plazo": None},
        ],
    },
    {
        "nombre": "Capital Pioneras Emprende",
        "emoji": "🌟",
        "link": "https://www.sercotec.cl/programas/capital-pioneras-emprende/",
        "monto_max": 3500000,
        "fecha_cierre": date(2027, 4, 30),
        "activo": True,
        "requisitos": [
            {"texto": "Mujer emprendedora (sexo registral femenino)", "clave": "genero_femenino", "recomendacion": "Puedes revisar Capital Semilla Emprende u otros fondos sin este requisito.", "plazo": None},
            {"texto": "Mayor de 18 años", "clave": "mayor_edad", "recomendacion": "Mientras tanto, puedes avanzar en capacitaciones y preparar tu proyecto.", "plazo": None},
            {"texto": "Sin inicio de actividades en primera categoría SII", "clave": "sin_inicio_sii", "recomendacion": "Si ya estás formalizado, revisa el fondo Crece.", "plazo": None},
            {"texto": "Rubro en sector no tradicional para mujeres", "clave": "rubro_pioneras", "recomendacion": "Este fondo aplica para rubros como manufactura, construcción, tecnología, transporte y minería.", "plazo": None},
            {"texto": "Presentar proyecto de negocio con video pitch (90 seg)", "clave": "proyecto_negocio", "recomendacion": "Prepara un video de 90 segundos presentándote y explicando tu idea de negocio.", "plazo": "1 a 2 días", "plazo_dias": 2},
        ],
    },
    {
        "nombre": "Crece",
        "emoji": "📈",
        "link": "https://www.sercotec.cl/programas/crece/",
        "monto_max": 5000000,
        "fecha_cierre": date(2027, 5, 31),
        "activo": True,
        "requisitos": [
            {"texto": "Inicio de actividades con más de 12 meses de antigüedad", "clave": "inicio_sii_12m", "recomendacion": "Haz tu inicio de actividades en sii.cl (gratis, 1 día). Luego debes esperar 12 meses para postular.", "plazo": "12 meses desde el inicio de actividades", "plazo_dias": 365},
            {"texto": "Ventas entre 200 y 25.000 UF/año", "clave": "ventas_crece", "recomendacion": "Cuéntame cuánto vendiste el último año y te digo si calificas.", "plazo": None},
            {"texto": "3 cursos aprobados en capacitacion.sercotec.cl", "clave": "capacitacion_crece", "recomendacion": "Completa 3 cursos gratuitos en capacitacion.sercotec.cl antes del cierre.", "plazo": "1 a 2 semanas", "plazo_dias": 14},
            {"texto": "Actividad económica coherente con la convocatoria", "clave": "actividad_coherente", "recomendacion": "Verifica en las bases de la convocatoria que tu rubro esté incluido.", "plazo": None},
        ],
    },
]


def _get_fondos_from_supabase() -> list[dict] | None:
    """Lee los fondos activos desde Supabase. Retorna None si no está disponible."""
    try:
        rows = list_active_funds()
        if not rows:
            return None

        fondos = []
        for row in rows:
            fecha_cierre = row.get("fecha_cierre")
            if isinstance(fecha_cierre, str):
                try:
                    fecha_cierre = datetime.strptime(fecha_cierre, "%Y-%m-%d").date()
                except ValueError:
                    logger.warning("Fecha inválida en fondo %s: %s", row.get("nombre"), fecha_cierre)
                    continue

            fondos.append({
                "id": row.get("id"),
                "nombre": row.get("nombre", ""),
                "emoji": row.get("emoji", "💰"),
                "entidad": row.get("entidad"),
                "link": row.get("link", ""),
                "monto_max": row.get("monto_max"),
                "fecha_cierre": fecha_cierre,
                "activo": row.get("activo", True),
                "requisitos": row.get("requisitos", []),
                "slug": row.get("slug"),
                "aliases": row.get("aliases") or [],
            })

        return fondos if fondos else None

    except Exception as error:
        logger.error("Error leyendo fondos desde Supabase: %s", error)
        return None


def _fondo_aplica_para_usuario(fondo: dict, is_formal: bool) -> bool:
    """Filtra fondos según si el usuario está formalizado o no."""
    requisitos = fondo.get("requisitos", [])
    claves = [r.get("clave") for r in requisitos]

    # Fondos que requieren NO tener inicio SII → solo para no formalizados
    if "sin_inicio_sii" in claves and is_formal:
        return False

    # Crece requiere SÍ tener inicio SII → solo para formalizados
    if "inicio_sii" in claves and not is_formal:
        return False

    return True


def evaluate_available_funds(
    user: dict,
    today: date | None = None,
    include_closed: bool = False,
) -> list[dict]:
    """Evalúa y ordena en una sola operación los fondos del perfil."""
    current_date = today or date.today()
    definitions = {}
    answers = {}
    try:
        definitions = get_requirement_definitions()
        if user.get("id"):
            answers = get_fund_answers(user["id"])
    except Exception as error:
        logger.warning(
            "No se pudo cargar el contexto estructurado de fondos: %s",
            error,
        )

    funds = _get_fondos_from_supabase() or FONDOS_FALLBACK
    evaluations = []
    for fund in funds:
        if not fund_applies_to_user(fund, user):
            continue
        evaluation = evaluate_fund(
            fund,
            user,
            answers,
            definitions,
            current_date,
        )
        if not include_closed and not evaluation["is_open"]:
            continue
        evaluations.append(evaluation)

    evaluations.sort(
        key=lambda evaluation: (
            0 if evaluation["is_open"] else 1,
            1 if evaluation["blocking_failures"] else 0,
            -evaluation["percentage"],
            (
                evaluation["days_remaining"]
                if evaluation["days_remaining"] is not None
                else float("inf")
            ),
            evaluation["fund"].get("nombre", ""),
        )
    )
    return evaluations


def format_funds_summary(evaluations: list[dict], max_length: int = 1000) -> str:
    """Construye un resumen compacto apto para una lista de WhatsApp."""
    header = "🎯 *Fondos disponibles para tu perfil*\n"
    footer = (
        "\n💡 Si quieres saber más sobre algún fondo, selecciónalo en la "
        "lista o escribe su nombre."
    )
    lines = [header]
    shown = 0

    for evaluation in evaluations:
        fund = evaluation["fund"]
        closing_date = fund.get("fecha_cierre")
        closing_text = (
            closing_date.strftime("%d/%m/%Y")
            if isinstance(closing_date, date)
            else "sin fecha"
        )
        status = "⛔ con bloqueo" if evaluation["blocking_failures"] else "✅ evaluable"
        block = (
            f"{fund.get('emoji', '💰')} *{fund.get('nombre', 'Fondo')}*\n"
            f"Compatibilidad: {evaluation['percentage']}% · {status}\n"
            f"Cierre: {closing_text} · "
            f"⚠️ {evaluation['unknown']} por confirmar\n"
        )
        candidate = "\n".join(lines + [block]) + footer
        if len(candidate) > max_length:
            break
        lines.append(block)
        shown += 1

    if shown < len(evaluations):
        lines.append(f"…y {len(evaluations) - shown} fondo(s) adicional(es).")
    lines.append(footer.lstrip("\n"))
    return "\n".join(lines)


def _urgencia_key(req: dict, dias_restantes_fondo: int) -> tuple:
    """Ordena los requisitos pendientes de mayor a menor urgencia.

    Compara el plazo estimado del requisito (`plazo_dias`) contra los días
    que quedan para el cierre del fondo:
    - Alcanzables a tiempo (holgura >= 0) van primero, del más urgente
      (menos holgura) al menos urgente.
    - No alcanzables a tiempo (holgura < 0) van después, del que estuvo más
      cerca de llegar a tiempo al que definitivamente no alcanza.
    - Sin plazo estimado definido (no comparable) van al final.
    """
    plazo_dias = req.get("plazo_dias")
    if plazo_dias is None:
        return (2, 0)

    holgura = dias_restantes_fondo - plazo_dias
    if holgura >= 0:
        return (0, holgura)
    return (1, -holgura)


def get_requirement_urgency(
    requirement: dict,
    days_remaining: int | None,
) -> dict:
    """Explica si una acción todavía alcanza antes del cierre."""
    duration = requirement.get("plazo_dias")
    if duration is None or days_remaining is None:
        return {
            "status": "unknown",
            "margin_days": None,
            "label": "ℹ️ Sin plazo suficiente para calcular alcanzabilidad",
        }

    margin = days_remaining - int(duration)
    if margin < 0:
        label = f"❌ No alcanzable para este cierre (faltan {-margin} días)"
        status = "not_reachable"
    elif margin == 0:
        label = "🚨 Debes comenzar inmediatamente"
        status = "immediate"
    elif margin <= 14:
        label = f"⚠️ Urgente, pero alcanzable (margen de {margin} días)"
        status = "urgent"
    else:
        label = f"✅ Todavía alcanzable (margen de {margin} días)"
        status = "reachable"

    return {
        "status": status,
        "margin_days": margin,
        "label": label,
    }


def format_fund_evaluation(evaluation: dict, user: dict) -> str:
    """Formatea el resultado detallado de un único fondo seleccionado."""
    fund = evaluation["fund"]
    closing_date = fund.get("fecha_cierre")
    days_remaining = evaluation.get("days_remaining")

    lines = [
        f"{fund.get('emoji', '💰')} *{fund.get('nombre', 'Fondo')}*",
    ]
    if isinstance(closing_date, date):
        if evaluation["is_open"]:
            lines.append(
                f"🟢 Cierre: {closing_date.strftime('%d/%m/%Y')} "
                f"({days_remaining} días)"
            )
        else:
            lines.append("🔴 Convocatoria cerrada")

    monto = fund.get("monto_max")
    if monto:
        lines.append(f"💵 Hasta ${monto:,.0f} CLP")

    lines.extend([
        f"Compatibilidad confirmada: *{evaluation['percentage']}%*",
        (
            "Resumen: "
            f"✅ {evaluation['met']} · "
            f"❌ {evaluation['failed']} · "
            f"⚠️ {evaluation['unknown']} por confirmar"
        ),
    ])
    if evaluation["blocking_failures"]:
        lines.append("\n⛔ *Existe un requisito excluyente no cumplido.*")

    requirements = evaluation["requirements"]
    completed = [req for req in requirements if req["cumple"] is True]
    unknown = [req for req in requirements if req["cumple"] is None]
    failed = [req for req in requirements if req["cumple"] is False]
    actionable = sorted(
        (req for req in failed if req not in evaluation["blocking_failures"]),
        key=lambda req: _urgencia_key(req, days_remaining or 0),
    )

    if completed:
        lines.append("\n✅ *Requisitos cumplidos*")
        lines.extend(f"• {requirement['texto']}" for requirement in completed)

    if unknown:
        lines.append("\n⚠️ *Información por confirmar*")
        lines.extend(f"• {requirement['texto']}" for requirement in unknown)

    if actionable:
        lines.append("\n📌 *Acciones recomendadas por urgencia*")
        for position, requirement in enumerate(actionable, start=1):
            lines.append(f"\n{position}. *{requirement['texto']}*")
            if requirement.get("plazo"):
                lines.append(f"⏱️ Tiempo estimado: {requirement['plazo']}")
            urgency = get_requirement_urgency(requirement, days_remaining)
            if requirement.get("plazo_dias") is not None:
                lines.append(urgency["label"])
            if requirement.get("recomendacion"):
                lines.append(f"💡 {requirement['recomendacion']}")

    if evaluation["blocking_failures"]:
        lines.append("\n⛔ *Requisitos excluyentes no cumplidos*")
        for requirement in evaluation["blocking_failures"]:
            lines.append(f"\n• *{requirement['texto']}*")
            if requirement.get("recomendacion"):
                lines.append(f"💡 {requirement['recomendacion']}")

    if not failed:
        lines.append("\n🎉 No hay requisitos confirmados como incumplidos.")

    if fund.get("link"):
        lines.append(f"\n🔗 Más información: {fund['link']}")
    lines.append(
        "\n💡 Si quieres saber más sobre algún fondo, escribe su nombre o "
        "*postular fondos*."
    )
    return "\n".join(lines)


def simulate_funds(user: dict) -> str:
    """
    Simula el proceso de postulación a fondos concursables.
    CA3: recomendaciones + plazo por cada ❌, con manejo especial para formalizado en fondos Emprende.
    CA4: fechas desde Supabase, prioriza vigentes.
    CA5: lee perfil actualizado sin caché.
    """
    is_formal = user.get("inicio_sii") == "si"
    rubro = user.get("rubro_raw") or user.get("rubro", "tu negocio")
    today = date.today()

    answers = {}
    definitions = {}
    try:
        definitions = get_requirement_definitions()
        if user.get("id"):
            answers = get_fund_answers(user["id"])
    except Exception as error:
        logger.warning(
            "No se cargaron respuestas o definiciones de fondos; "
            "se usará la evaluación compatible anterior: %s",
            error,
        )

    fondos_raw = _get_fondos_from_supabase()
    if not fondos_raw:
        fondos_raw = FONDOS_FALLBACK
        logger.warning("Usando fondos hardcodeados (Supabase no disponible)")

    fondos_filtrados = [f for f in fondos_raw if _fondo_aplica_para_usuario(f, is_formal)]
    vigentes = [f for f in fondos_filtrados if f["fecha_cierre"] >= today]
    cerrados = [f for f in fondos_filtrados if f["fecha_cierre"] < today]
    fondos_ordenados = sorted(vigentes, key=lambda f: f["fecha_cierre"]) + cerrados

    lines = [
        "🎯 *Simulación de Fondos Concursables*\n",
        f"Perfil: _{rubro.capitalize()}_ · "
        f"{'✅ Formalizado' if is_formal else '⚠️ No formalizado'}\n",
    ]

    if not vigentes:
        lines.append(
            "⚠️ _No hay fondos con convocatoria vigente en este momento. "
            "Te avisaré cuando abran nuevas convocatorias._\n"
        )

    for fondo in fondos_ordenados:
        nombre = fondo["nombre"]
        emoji = fondo.get("emoji", "💰")
        fecha_cierre = fondo["fecha_cierre"]
        monto = fondo.get("monto_max")
        link = fondo.get("link", "")
        evaluation = evaluate_fund(
            fondo,
            user,
            answers,
            definitions,
            today,
        )
        reqs_evaluados = evaluation["requirements"]
        dias_restantes_fondo = evaluation["days_remaining"]
        pct = evaluation["percentage"]

        if fecha_cierre < today:
            estado_conv = "🔴 Convocatoria cerrada"
        else:
            estado_conv = (
                f"🟢 Cierre: {fecha_cierre.strftime('%d/%m/%Y')} "
                f"({dias_restantes_fondo} días)"
            )

        monto_texto = f" · Hasta ${monto:,.0f} CLP" if monto else ""
        lines.append(f"\n*{emoji} {nombre}*")
        lines.append(f"{estado_conv}{monto_texto}")
        lines.append(f"Compatibilidad: *{pct}%*")
        if evaluation["unknown"]:
            lines.append(
                f"Información pendiente: *{evaluation['unknown']} requisito(s)*"
            )
        if evaluation["blocking_failures"]:
            lines.append("⛔ Existe al menos un requisito excluyente no cumplido.")

        # CA2 (HdU05 backlog Sprint 1): los requisitos pendientes (no
        # cumplidos) se muestran ordenados de mayor a menor urgencia; los
        # ya cumplidos quedan primero, en su orden original.
        cumplidos = [r for r in reqs_evaluados if r["cumple"] is True]
        pendientes = sorted(
            (r for r in reqs_evaluados if r["cumple"] is not True),
            key=lambda r: _urgencia_key(r, dias_restantes_fondo),
        )
        reqs_ordenados = cumplidos + pendientes

        for req in reqs_ordenados:
            lines.extend(_get_mensaje_requisito(req, req["cumple"], is_formal))

        if link:
            lines.append(f"  🔗 _Más info: {link}_")

    if vigentes:
        lines.append(
            "\n💡 ¿Quieres que te ayude a cumplir los requisitos que te faltan? "
            "Escribe el nombre del fondo y te oriento paso a paso."
        )
    else:
        lines.append(
            "\n📌 Cuando abran nuevas convocatorias te avisaré. "
            "Mientras tanto, puedes avanzar en tu roadmap de formalización."
        )

    return "\n".join(lines)
