"""Acceso a `expert_requests` y `expert_messages` (HdU16: contacto con un experto).

Usa el cliente administrativo (service role): estas tablas no tienen
políticas RLS, solo las toca el backend.
"""
import logging
from datetime import datetime, timezone

import dependencies

logger = logging.getLogger("financial")

OPEN_STATUSES = ("pending", "in_progress")


def _client():
    client = dependencies.supabase_admin
    if client is None:
        raise RuntimeError("Falta SUPABASE_SERVICE_ROLE_KEY para las solicitudes de experto")
    return client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_open_expert_request(user_id: str) -> dict | None:
    result = (
        _client().table("expert_requests").select("*")
        .eq("user_id", user_id).in_("status", list(OPEN_STATUSES))
        .limit(1).execute()
    )
    return result.data[0] if result.data else None


def create_expert_request(user_id: str, comuna: str) -> dict | None:
    result = (
        _client().table("expert_requests")
        .insert({"user_id": user_id, "comuna": comuna}).execute()
    )
    return result.data[0] if result.data else None


def get_expert_request(request_id: str, comuna: str) -> dict | None:
    """Trae una solicitud solo si pertenece a la comuna del admin."""
    result = (
        _client().table("expert_requests").select("*, users(phone, rubro, roadmap, roadmap_completed_at)")
        .eq("id", request_id).eq("comuna", comuna).limit(1).execute()
    )
    return result.data[0] if result.data else None


def list_expert_requests(comuna: str, statuses: tuple[str, ...], limit: int = 50) -> list[dict]:
    result = (
        _client().table("expert_requests").select("*, users(rubro)")
        .eq("comuna", comuna).in_("status", list(statuses))
        .order("created_at", desc=True).limit(limit).execute()
    )
    return result.data or []


def take_expert_request(request_id: str, comuna: str, taken_by: str) -> bool:
    """pending -> in_progress. False si ya no estaba pendiente."""
    result = (
        _client().table("expert_requests")
        .update({"status": "in_progress", "taken_at": _now(), "taken_by": taken_by})
        .eq("id", request_id).eq("comuna", comuna).eq("status", "pending").execute()
    )
    return bool(result.data)


def resolve_expert_request(request_id: str, closed_by: str, comuna: str | None = None) -> bool:
    query = (
        _client().table("expert_requests")
        .update({"status": "resolved", "resolved_at": _now(), "closed_by": closed_by})
        .eq("id", request_id).in_("status", list(OPEN_STATUSES))
    )
    if comuna:
        query = query.eq("comuna", comuna)
    return bool(query.execute().data)


def touch_user_message(request_id: str) -> None:
    """Registra un mensaje entrante del emprendedor (renueva la ventana de 24 h)."""
    _client().table("expert_requests").update(
        {"user_last_message_at": _now()}
    ).eq("id", request_id).execute()


def add_expert_message(request_id: str, sender: str, content: str) -> None:
    _client().table("expert_messages").insert(
        {"request_id": request_id, "sender": sender, "content": content}
    ).execute()


def list_expert_messages(request_id: str) -> list[dict]:
    result = (
        _client().table("expert_messages").select("sender, content, created_at")
        .eq("request_id", request_id).order("created_at").execute()
    )
    return result.data or []
