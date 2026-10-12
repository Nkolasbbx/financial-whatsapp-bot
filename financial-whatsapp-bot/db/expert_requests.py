"""Acceso a `expert_requests` y `expert_messages` (HdU16: contacto con un experto).

Usa el cliente administrativo (service role): estas tablas no tienen
políticas RLS, solo las toca el backend.
"""
import logging
import re
from datetime import datetime, timezone

import dependencies

logger = logging.getLogger("financial")

# 'draft' = el bot todavía le está preguntando el tema/motivo al emprendedor.
OPEN_STATUSES = ("draft", "pending", "in_progress")
RESOLVABLE_STATUSES = ("pending", "in_progress")

_USER_FIELDS = "rubro, inicio_sii, roadmap, roadmap_completed_at"


def _client():
    client = dependencies.supabase_admin
    if client is None:
        raise RuntimeError("Falta SUPABASE_SERVICE_ROLE_KEY para las solicitudes de experto")
    return client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_timestamp(value: str | None) -> datetime | None:
    """Parsea un timestamp de Postgres (con 1 a 6 decimales, o 'Z')."""
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    # fromisoformat (Python 3.10) exige 3 o 6 decimales.
    text = re.sub(r"\.(\d+)", lambda m: "." + m.group(1).ljust(6, "0")[:6], text)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def get_open_expert_request(user_id: str) -> dict | None:
    result = (
        _client().table("expert_requests").select("*")
        .eq("user_id", user_id).in_("status", list(OPEN_STATUSES))
        .limit(1).execute()
    )
    return result.data[0] if result.data else None


def create_expert_request(user_id: str, comuna: str, status: str = "pending") -> dict | None:
    result = (
        _client().table("expert_requests")
        .insert({"user_id": user_id, "comuna": comuna, "status": status}).execute()
    )
    return result.data[0] if result.data else None


def set_expert_topic(request_id: str, topic: str) -> None:
    _client().table("expert_requests").update({"topic": topic}).eq("id", request_id).execute()


def finalize_expert_request(request_id: str, reason: str | None) -> bool:
    """draft -> pending, con el motivo (opcional) que escribió el emprendedor."""
    result = (
        _client().table("expert_requests")
        .update({"status": "pending", "reason": reason, "user_last_message_at": _now()})
        .eq("id", request_id).eq("status", "draft").execute()
    )
    return bool(result.data)


def delete_expert_draft(request_id: str) -> None:
    """Descarta un borrador (el emprendedor canceló o lo dejó abandonado)."""
    _client().table("expert_requests").delete().eq("id", request_id).eq("status", "draft").execute()


def get_expert_request(request_id: str, comuna: str) -> dict | None:
    """Trae una solicitud solo si pertenece a la comuna del admin."""
    result = (
        _client().table("expert_requests").select(f"*, users(phone, {_USER_FIELDS})")
        .eq("id", request_id).eq("comuna", comuna).neq("status", "draft")
        .limit(1).execute()
    )
    return result.data[0] if result.data else None


def list_expert_requests(comuna: str, statuses: tuple[str, ...], limit: int = 50) -> list[dict]:
    result = (
        _client().table("expert_requests").select(f"*, users({_USER_FIELDS})")
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
        .eq("id", request_id).in_("status", list(RESOLVABLE_STATUSES))
    )
    if comuna:
        query = query.eq("comuna", comuna)
    return bool(query.execute().data)


def touch_user_message(request_id: str) -> None:
    """Registra un mensaje entrante del emprendedor (renueva la ventana de 24 h)."""
    _client().table("expert_requests").update(
        {"user_last_message_at": _now()}
    ).eq("id", request_id).execute()


def add_expert_message(
    request_id: str,
    sender: str,
    content: str,
    wa_message_id: str | None = None,
    reply_to_id: int | None = None,
) -> None:
    """`wa_message_id`: wamid del mensaje recibido (para poder citarlo después).
    `reply_to_id`: id del mensaje de esta conversación al que responde el experto."""
    row = {"request_id": request_id, "sender": sender, "content": content}
    if wa_message_id:
        row["wa_message_id"] = wa_message_id
    if reply_to_id:
        row["reply_to_id"] = reply_to_id
    _client().table("expert_messages").insert(row).execute()


def list_expert_messages(request_id: str) -> list[dict]:
    result = (
        _client().table("expert_messages")
        .select("id, sender, content, created_at, wa_message_id, reply_to_id")
        .eq("request_id", request_id).order("created_at").execute()
    )
    return result.data or []
