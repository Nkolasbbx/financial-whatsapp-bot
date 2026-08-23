"""
FinancIAl — db/alertas.py

Capa de persistencia para alertas tributarias y de fondos (HdU07).
Sigue el mismo patrón que db/reminders.py.
"""

import logging
from datetime import date, datetime, timezone

logger = logging.getLogger("financial")


def _admin_client():
    import dependencies
    if dependencies.supabase_admin is None:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY no está configurada; "
            "no se pueden procesar alertas tributarias"
        )
    return dependencies.supabase_admin


def _optional_client():
    import dependencies
    return dependencies.supabase_admin


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def get_users_for_tax_alerts() -> list[dict]:
    """
    CA1: Retorna usuarios formalizados con alertas tributarias activadas.
    """
    client = _admin_client()
    result = (
        client.table("users")
        .select("id, phone, inicio_sii, rubro, rubro_raw, comuna")
        .eq("inicio_sii", "si")
        .eq("onboarding_step", "done")
        .execute()
    )
    return result.data or []


def get_users_for_fund_alerts() -> list[dict]:
    """
    CA2: Retorna usuarios NO formalizados con onboarding completado.
    """
    client = _admin_client()
    result = (
        client.table("users")
        .select("id, phone, inicio_sii, rubro, rubro_raw, comuna")
        .eq("inicio_sii", "no")
        .eq("onboarding_step", "done")
        .execute()
    )
    return result.data or []


def alerta_ya_enviada(user_id: str, tipo: str, fecha_vencimiento: date) -> bool:
    """
    Verifica si ya se envió esta alerta para evitar duplicados.
    Usa la tabla alert_deliveries.
    """
    client = _optional_client()
    if client is None:
        return False

    try:
        result = (
            client.table("alert_deliveries")
            .select("id")
            .eq("user_id", user_id)
            .eq("tipo", tipo)
            .eq("fecha_referencia", fecha_vencimiento.isoformat())
            .neq("delivery_status", "failed")
            .limit(1)
            .execute()
        )
        return bool(result.data)
    except Exception as error:
        logger.error("Error verificando alerta duplicada: %s", error)
        return False


def registrar_alerta_enviada(
    user_id: str,
    tipo: str,
    nombre: str,
    fecha_referencia: date,
    provider_message_id: str | None = None,
) -> str | None:
    """
    Registra el envío de una alerta tributaria o de fondo.
    Retorna el ID del registro creado.
    """
    client = _optional_client()
    if client is None:
        return None

    try:
        result = (
            client.table("alert_deliveries")
            .insert({
                "user_id": user_id,
                "tipo": tipo,
                "nombre": nombre,
                "fecha_referencia": fecha_referencia.isoformat(),
                "delivery_status": "sent" if provider_message_id else "pending",
                "provider_message_id": provider_message_id,
                "sent_at": _iso_utc(_utc_now()) if provider_message_id else None,
            })
            .execute()
        )
        return result.data[0]["id"] if result.data else None
    except Exception as error:
        logger.error("Error registrando alerta enviada: %s", error)
        return None


def marcar_alerta_fallida(alert_id: str, razon: str) -> None:
    """Marca una alerta como fallida."""
    client = _optional_client()
    if client is None:
        return
    try:
        client.table("alert_deliveries").update({
            "delivery_status": "failed",
            "failure_reason": razon[:2000],
        }).eq("id", alert_id).execute()
    except Exception as error:
        logger.error("Error marcando alerta como fallida: %s", error)