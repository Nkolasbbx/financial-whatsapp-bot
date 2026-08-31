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

logger = logging.getLogger("financial")


def _evaluar_requisito(clave: str, user: dict) -> bool | None:
    """Evalúa si el usuario cumple un requisito dado su clave."""
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
            {"texto": "Persona natural mayor de 18 años", "clave": "mayor_edad", "recomendacion": None, "plazo": None},
            {"texto": "Sin inicio de actividades en el SII", "clave": "sin_inicio_sii", "recomendacion": None, "plazo": None},
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
            {"texto": "Mujer emprendedora (sexo registral femenino)", "clave": "genero_femenino", "recomendacion": None, "plazo": None},
            {"texto": "Mayor de 18 años", "clave": "mayor_edad", "recomendacion": None, "plazo": None},
            {"texto": "Sin inicio de actividades en primera categoría SII", "clave": "sin_inicio_sii", "recomendacion": None, "plazo": None},
            {"texto": "Sin beneficios SERCOTEC en los últimos 2 años", "clave": "sin_beneficio_reciente", "recomendacion": None, "plazo": None},
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
            {"texto": "Mujer emprendedora (sexo registral femenino)", "clave": "genero_femenino", "recomendacion": None, "plazo": None},
            {"texto": "Mayor de 18 años", "clave": "mayor_edad", "recomendacion": None, "plazo": None},
            {"texto": "Sin inicio de actividades en primera categoría SII", "clave": "sin_inicio_sii", "recomendacion": None, "plazo": None},
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
    import dependencies

    if not dependencies.supabase:
        return None

    try:
        result = (
            dependencies.supabase
            .table("fondos")
            .select("*")
            .eq("activo", True)
            .order("fecha_cierre", desc=False)
            .execute()
        )

        if not result.data:
            return None

        fondos = []
        for row in result.data:
            fecha_cierre = row.get("fecha_cierre")
            if isinstance(fecha_cierre, str):
                try:
                    fecha_cierre = datetime.strptime(fecha_cierre, "%Y-%m-%d").date()
                except ValueError:
                    logger.warning("Fecha inválida en fondo %s: %s", row.get("nombre"), fecha_cierre)
                    continue

            fondos.append({
                "nombre": row.get("nombre", ""),
                "emoji": row.get("emoji", "💰"),
                "link": row.get("link", ""),
                "monto_max": row.get("monto_max"),
                "fecha_cierre": fecha_cierre,
                "activo": row.get("activo", True),
                "requisitos": row.get("requisitos", []),
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
        requisitos = fondo.get("requisitos", [])

        dias_restantes_fondo = (fecha_cierre - today).days

        reqs_evaluados = []
        for req in requisitos:
            clave = req.get("clave", "")
            cumple = _evaluar_requisito(clave, user)
            reqs_evaluados.append({
                "texto": req.get("texto", ""),
                "clave": clave,
                "cumple": cumple,
                "recomendacion": req.get("recomendacion"),
                "plazo": req.get("plazo"),
                "plazo_dias": req.get("plazo_dias"),
            })

        met = sum(1 for r in reqs_evaluados if r["cumple"] is True)
        total = len(reqs_evaluados)
        pct = round((met / total) * 100) if total > 0 else 0

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