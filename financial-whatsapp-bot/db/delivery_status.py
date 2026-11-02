"""Estados de entrega de Meta en una llamada (HDU optimización, P-08)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("financial")

RPC_NAME = "apply_delivery_status_v1"


class DeliveryStatusUnavailable(RuntimeError):
    """La RPC no está disponible: usar las actualizaciones por tabla."""


def _event_time(event_timestamp) -> str | None:
    try:
        return datetime.fromtimestamp(int(event_timestamp), tz=timezone.utc).isoformat() if event_timestamp else None
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def apply_delivery_status(
    provider_message_id: str,
    status: str,
    event_timestamp=None,
    failure_reason: str | None = None,
) -> str | None:
    """Aplica el estado en la tabla dueña del wamid. Devuelve cuál o None.

    Lanza DeliveryStatusUnavailable si la RPC no existe o falla, para que el
    webhook use el camino anterior sin perder el estado.
    """
    import dependencies

    if not provider_message_id or not status:
        return None
    client = getattr(dependencies, "supabase_admin", None)
    if client is None:
        raise DeliveryStatusUnavailable("Falta el cliente administrativo")
    try:
        data = client.rpc(RPC_NAME, {
            "p_provider_message_id": provider_message_id,
            "p_status": status,
            "p_event_at": _event_time(event_timestamp),
            "p_failure_reason": failure_reason,
        }).execute().data
    except Exception as error:
        raise DeliveryStatusUnavailable(type(error).__name__) from error
    if isinstance(data, list):
        data = data[0] if data else None
    return data if isinstance(data, str) else None
