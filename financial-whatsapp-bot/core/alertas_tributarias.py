"""
FinancIAl — core/alertas_tributarias.py

Calendario tributario del SII y lógica de alertas.

Cubre los criterios de aceptación de HdU07:
- CA1: Emprendedor formalizado → alerta 5 días antes del vencimiento
        con fecha límite, trámite y link al SII.
- CA2: Emprendedor no formalizado → no recibe alertas tributarias,
        pero sí notificaciones de convocatorias de fondos relevantes.
"""

from datetime import date, timedelta
import logging

logger = logging.getLogger("financial")


def get_calendario_sii(year: int) -> list[dict]:
    """
    Retorna el calendario tributario del SII para el año dado.
    Fuente: sii.cl/contribuyentes/calendario_tributario.html
    """
    calendar = []

    # ── Formulario 29 (IVA + PPM) ─────────────────────────────────────────
    # Vence el día 12 de cada mes para contribuyentes del régimen general.
    for mes in range(1, 13):
        mes_vencimiento = mes + 1 if mes < 12 else 1
        year_vencimiento = year if mes < 12 else year + 1

        try:
            fecha = date(year_vencimiento, mes_vencimiento, 12)
        except ValueError:
            continue

        nombre_mes = [
            "enero", "febrero", "marzo", "abril", "mayo", "junio",
            "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
        ][mes - 1]

        calendar.append({
            "nombre": f"Declaración IVA y PPM (F29) — {nombre_mes} {year}",
            "descripcion": (
                f"Declara y paga el IVA del mes de {nombre_mes}. "
                f"Se hace en sii.cl con tu RUT y clave. "
                f"Si no tienes ventas ese mes, igual debes declarar con monto 0."
            ),
            "fecha_vencimiento": fecha,
            "link": "https://homer.sii.cl/",
            "tipo": "f29",
            "aplica_a": "formalizado",
        })

    # ── Declaración Renta Anual (F22) ──────────────────────────────────────
    calendar.append({
        "nombre": f"Declaración de Renta Anual (F22) — año {year - 1}",
        "descripcion": (
            "Declara los ingresos del año anterior. "
            "Se hace en sii.cl → Renta → Declarar. "
            "Si tienes inicio de actividades, es obligatorio aunque no hayas vendido."
        ),
        "fecha_vencimiento": date(year, 4, 30),
        "link": "https://www.sii.cl/servicios_online/1039-.html",
        "tipo": "f22",
        "aplica_a": "formalizado",
    })

    # ── Patente Municipal ──────────────────────────────────────────────────
    calendar.append({
        "nombre": f"Pago Patente Municipal — 1er semestre {year}",
        "descripcion": (
            "Paga la patente comercial del primer semestre en tu municipalidad. "
            "Puedes hacerlo en la sucursal o en el sitio web de tu municipio."
        ),
        "fecha_vencimiento": date(year, 1, 31),
        "link": "https://www.municipalidadderecoleta.cl/",
        "tipo": "patente",
        "aplica_a": "formalizado",
    })
    calendar.append({
        "nombre": f"Pago Patente Municipal — 2do semestre {year}",
        "descripcion": (
            "Paga la patente comercial del segundo semestre en tu municipalidad. "
            "Puedes hacerlo en la sucursal o en el sitio web de tu municipio."
        ),
        "fecha_vencimiento": date(year, 7, 31),
        "link": "https://www.municipalidadderecoleta.cl/",
        "tipo": "patente",
        "aplica_a": "formalizado",
    })

    return calendar


def get_alertas_proximas(
    user: dict,
    dias_anticipacion: int = 5,
    hoy: date | None = None,
) -> list[dict]:
    """
    CA1: Retorna alertas tributarias que vencen en los próximos N días.
    CA2: Usuarios no formalizados retornan lista vacía.
    """
    is_formal = user.get("inicio_sii") == "si"

    if not is_formal:
        return []

    today = hoy or date.today()
    limite = today + timedelta(days=dias_anticipacion)
    year = today.year

    calendario = get_calendario_sii(year)
    if today.month == 12:
        calendario += get_calendario_sii(year + 1)

    alertas = []
    for evento in calendario:
        fecha = evento["fecha_vencimiento"]
        if today <= fecha <= limite:
            alertas.append({
                **evento,
                "dias_restantes": (fecha - today).days,
            })

    return sorted(alertas, key=lambda a: a["fecha_vencimiento"])


def get_alertas_fondos_proximos(
    user: dict,
    dias_anticipacion: int = 7,
    hoy: date | None = None,
) -> list[dict]:
    """
    CA2: Para usuarios NO formalizados, retorna fondos con convocatoria
    que cierra en los próximos N días.
    """
    is_formal = user.get("inicio_sii") == "si"

    if is_formal:
        return []

    today = hoy or date.today()
    limite = today + timedelta(days=dias_anticipacion)

    try:
        import dependencies
        if not dependencies.supabase:
            return []

        result = (
            dependencies.supabase
            .table("fondos")
            .select("nombre, fecha_cierre, link, emoji")
            .eq("activo", True)
            .gte("fecha_cierre", today.isoformat())
            .lte("fecha_cierre", limite.isoformat())
            .order("fecha_cierre")
            .execute()
        )

        fondos = []
        for row in result.data or []:
            fecha_cierre_str = row.get("fecha_cierre", "")
            try:
                from datetime import datetime
                fecha_cierre = datetime.strptime(fecha_cierre_str, "%Y-%m-%d").date()
            except ValueError:
                continue

            fondos.append({
                "nombre": row.get("nombre", ""),
                "emoji": row.get("emoji", "💰"),
                "fecha_cierre": fecha_cierre,
                "link": row.get("link", ""),
                "dias_restantes": (fecha_cierre - today).days,
            })

        return fondos

    except Exception as error:
        logger.error("Error leyendo fondos próximos: %s", error)
        return []


def format_alerta_tributaria(alerta: dict) -> str:
    """CA1: Formatea mensaje de alerta tributaria para WhatsApp."""
    dias = alerta["dias_restantes"]
    if dias == 0:
        urgencia = "⚠️ *¡Vence HOY!*"
    elif dias == 1:
        urgencia = "⚠️ *¡Vence mañana!*"
    else:
        urgencia = f"📅 Vence en *{dias} días*"

    fecha_str = alerta["fecha_vencimiento"].strftime("%d/%m/%Y")

    return (
        f"🔔 *Alerta Tributaria — FinancIAl*\n\n"
        f"{urgencia}\n"
        f"📋 *{alerta['nombre']}*\n\n"
        f"_{alerta['descripcion']}_\n\n"
        f"📅 Fecha límite: *{fecha_str}*\n"
        f"🔗 {alerta['link']}"
    )


def format_alerta_fondo(fondo: dict) -> str:
    """CA2: Formatea mensaje de notificación de fondo próximo a cerrar."""
    dias = fondo["dias_restantes"]
    if dias == 0:
        urgencia = "¡Cierra *HOY*!"
    elif dias == 1:
        urgencia = "¡Cierra *mañana*!"
    else:
        urgencia = f"Cierra en *{dias} días*"

    fecha_str = fondo["fecha_cierre"].strftime("%d/%m/%Y")

    return (
        f"💡 *Fondo concursable próximo a cerrar — FinancIAl*\n\n"
        f"{fondo['emoji']} *{fondo['nombre']}*\n"
        f"{urgencia} ({fecha_str})\n\n"
        f"_Este fondo puede financiar tu emprendimiento. "
        f"Para postular necesitarás formalizar tu negocio primero._\n\n"
        f"📋 Escribe *'mi roadmap'* para ver tus pasos de formalización\n"
        f"🔗 Más info: {fondo['link']}"
    )