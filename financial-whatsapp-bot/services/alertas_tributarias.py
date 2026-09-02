"""
FinancIAl — services/alertas_tributarias.py

Servicio de envío de alertas tributarias y de fondos (HdU07).
Sigue el mismo patrón que services/reminders.py.
Se ejecuta desde el cron job existente o desde un endpoint separado.
"""

import asyncio
import logging
from datetime import date

from core.alertas_tributarias import (
    format_alerta_fondo,
    format_alerta_tributaria,
    get_alertas_fondos_proximos,
    get_alertas_proximas,
)
from core.menu import INTERACTIVE_BODY_LIMIT, MENU_BUTTON
from db.alertas import (
    alerta_ya_enviada,
    get_users_for_fund_alerts,
    get_users_for_tax_alerts,
    marcar_alerta_fallida,
    registrar_alerta_enviada,
)
from services.message_router import split_message
from services.whatsapp import (
    send_interactive_buttons,
    send_template_with_menu_followup,
    send_text,
)

logger = logging.getLogger("financial")

# Días de anticipación para cada tipo de alerta
DIAS_ANTICIPACION_TRIBUTARIA = 30  # CA1: 30 días antes del vencimiento (F29)
DIAS_ANTICIPACION_FONDOS = 7       # CA2: 7 días antes del cierre


async def _send_fund_alert_with_menu(phone: str, mensaje: str) -> None:
    """CA2: envía la alerta de fondo con botón de Menú Principal.

    format_alerta_fondo() genera mensajes cortos y de longitud acotada, pero
    se aplica el mismo guard de 1024 caracteres que core/ia.py como red de
    seguridad ante textos de fondo inusualmente largos (no es el camino común).
    """
    if len(mensaje) <= INTERACTIVE_BODY_LIMIT:
        await send_interactive_buttons(phone, mensaje, MENU_BUTTON)
        return

    for part in split_message(mensaje, 3500):
        await send_text(phone, part)
    await send_interactive_buttons(phone, "📱 Volver al menú principal:", MENU_BUTTON)


async def send_tax_alerts() -> dict:
    """
    CA1: Envía alertas tributarias a usuarios formalizados.
    CA2: Envía notificaciones de fondos a usuarios no formalizados.

    Se llama desde el cron job junto con send_due_reminders().
    """
    today = date.today()
    counters = {
        "status": "completed",
        "tax_alerts_sent": 0,
        "fund_alerts_sent": 0,
        "failed": 0,
        "skipped": 0,
    }

    # ── CA1: Alertas tributarias para usuarios formalizados ──────────────────
    users_formalizados = await asyncio.to_thread(get_users_for_tax_alerts)
    logger.info(
        "Procesando alertas tributarias para %d usuarios formalizados",
        len(users_formalizados),
    )

    for user in users_formalizados:
        alertas = get_alertas_proximas(
            user,
            dias_anticipacion=DIAS_ANTICIPACION_TRIBUTARIA,
            hoy=today,
        )

        for alerta in alertas:
            tipo = alerta["tipo"]
            fecha_vencimiento = alerta["fecha_vencimiento"]
            user_id = user["id"]

            # Verificar si ya se envió esta alerta (evitar duplicados)
            ya_enviada = await asyncio.to_thread(
                alerta_ya_enviada,
                user_id,
                tipo,
                fecha_vencimiento,
            )
            if ya_enviada:
                counters["skipped"] += 1
                continue

            # Registrar intento antes de enviar
            alert_id = await asyncio.to_thread(
                registrar_alerta_enviada,
                user_id,
                tipo,
                alerta["nombre"],
                fecha_vencimiento,
                None,
            )

            try:
                # CA1: usar plantilla aprobada por Meta para mensajes proactivos
                fecha_str = alerta["fecha_vencimiento"].strftime("%d de %B de %Y")
                # Traducir mes al español
                meses = {
                    "January": "enero", "February": "febrero", "March": "marzo",
                    "April": "abril", "May": "mayo", "June": "junio",
                    "July": "julio", "August": "agosto", "September": "septiembre",
                    "October": "octubre", "November": "noviembre", "December": "diciembre"
                }
                for en, es in meses.items():
                    fecha_str = fecha_str.replace(en, es)

                await send_template_with_menu_followup(
                    user["phone"],
                    "recordatorio_fecha_tributaria",
                    "es_CL",
                    [
                        "Emprendedor/a",
                        alerta["nombre"].split("—")[0].strip(),
                        fecha_str,
                    ]
                )

                # Actualizar registro con éxito
                await asyncio.to_thread(
                    registrar_alerta_enviada,
                    user_id,
                    tipo,
                    alerta["nombre"],
                    fecha_vencimiento,
                    "sent",
                )
                counters["tax_alerts_sent"] += 1
                logger.info(
                    "Alerta tributaria '%s' enviada al usuario %s",
                    alerta["nombre"],
                    user_id,
                )

            except Exception as error:
                logger.exception(
                    "No se pudo enviar alerta tributaria '%s' al usuario %s: %s",
                    alerta["nombre"],
                    user_id,
                    error,
                )
                if alert_id:
                    await asyncio.to_thread(
                        marcar_alerta_fallida,
                        alert_id,
                        str(error),
                    )
                counters["failed"] += 1

    # ── CA2: Notificaciones de fondos para usuarios NO formalizados ──────────
    users_no_formalizados = await asyncio.to_thread(get_users_for_fund_alerts)
    logger.info(
        "Procesando alertas de fondos para %d usuarios no formalizados",
        len(users_no_formalizados),
    )

    for user in users_no_formalizados:
        fondos_proximos = get_alertas_fondos_proximos(
            user,
            dias_anticipacion=DIAS_ANTICIPACION_FONDOS,
            hoy=today,
        )

        for fondo in fondos_proximos:
            user_id = user["id"]
            tipo = f"fondo_{fondo['nombre'][:30]}"
            fecha_cierre = fondo["fecha_cierre"]

            ya_enviada = await asyncio.to_thread(
                alerta_ya_enviada,
                user_id,
                tipo,
                fecha_cierre,
            )
            if ya_enviada:
                counters["skipped"] += 1
                continue

            alert_id = await asyncio.to_thread(
                registrar_alerta_enviada,
                user_id,
                tipo,
                fondo["nombre"],
                fecha_cierre,
                None,
            )

            try:
                mensaje = format_alerta_fondo(fondo)
                await _send_fund_alert_with_menu(user["phone"], mensaje)

                await asyncio.to_thread(
                    registrar_alerta_enviada,
                    user_id,
                    tipo,
                    fondo["nombre"],
                    fecha_cierre,
                    "sent",
                )
                counters["fund_alerts_sent"] += 1
                logger.info(
                    "Alerta de fondo '%s' enviada al usuario %s",
                    fondo["nombre"],
                    user_id,
                )

            except Exception as error:
                logger.exception(
                    "No se pudo enviar alerta de fondo '%s' al usuario %s: %s",
                    fondo["nombre"],
                    user_id,
                    error,
                )
                if alert_id:
                    await asyncio.to_thread(
                        marcar_alerta_fallida,
                        alert_id,
                        str(error),
                    )
                counters["failed"] += 1

    return counters