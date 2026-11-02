"""Contexto de un mensaje en una sola llamada (HDU optimización, P-06).

get_message_context devuelve el perfil y todos los estados de conversación que
route_message consultaba uno por uno. Si la RPC no está disponible o falla,
devuelve None y el llamador usa las consultas anteriores: nunca bloquea un
mensaje por un problema de este atajo.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("financial")

RPC_NAME = "get_message_context_v1"


class NotLoaded:
    """Marca un dato que la RPC no trajo: el consumidor debe leerlo como antes."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "NOT_LOADED"

    def __bool__(self) -> bool:
        return False


NOT_LOADED = NotLoaded()


@dataclass
class MessageContext:
    user: dict | None
    expert_request: dict | None = None
    formalization: Any = NOT_LOADED
    fund_session: dict | None = None
    fund_definition: dict | None = None
    financial_session: dict | None = None
    calendar_session: dict | None = None
    # Se vuelve True si algo escribe estado durante el mensaje (por ejemplo,
    # el consentimiento de audio): a partir de ahí se vuelve a leer como antes.
    stale: bool = field(default=False)

    @property
    def blocked(self) -> bool:
        return bool(self.user and self.user.get("bloqueado"))


def _normalize_user(user: dict | None) -> dict | None:
    if not isinstance(user, dict):
        return None
    # La columna roadmap es JSONB; compatibilidad con texto JSON, como get_user.
    if isinstance(user.get("roadmap"), str):
        try:
            user["roadmap"] = json.loads(user["roadmap"])
        except json.JSONDecodeError:
            logger.warning("El roadmap del usuario no contiene JSON válido")
            user["roadmap"] = None
    return user


def _active_fund_session(session: dict | None) -> dict | None:
    from db.fondos import ACTIVE_SESSION_STATUSES

    if not isinstance(session, dict):
        return None
    return session if session.get("status") in ACTIVE_SESSION_STATUSES else None


def _formalization(value: Any) -> Any:
    """Contexto del diagnóstico validado igual que get_formalization_context."""
    from db.formalization_diagnostics import _context

    if not isinstance(value, dict) or "error" in value:
        return NOT_LOADED
    try:
        return _context(value)
    except Exception:
        return NOT_LOADED


def parse_message_context(data: Any) -> MessageContext | None:
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if not isinstance(data, dict) or data.get("version") != 1 or "user" not in data:
        return None
    user = _normalize_user(data.get("user"))
    if user is None:
        # Teléfono sin perfil: route_message lo crea y lee el resto como antes.
        return MessageContext(user=None)
    fund_session = _active_fund_session(data.get("fund_session"))
    return MessageContext(
        user=user,
        expert_request=data.get("expert_request") if isinstance(data.get("expert_request"), dict) else None,
        formalization=_formalization(data.get("formalization")),
        fund_session=fund_session,
        fund_definition=(
            data.get("fund_definition")
            if fund_session and fund_session.get("pending_field_key") and isinstance(data.get("fund_definition"), dict)
            else None
        ),
        financial_session=data.get("financial_session") if isinstance(data.get("financial_session"), dict) else None,
        calendar_session=data.get("calendar_session") if isinstance(data.get("calendar_session"), dict) else None,
    )


def get_message_context(phone: str) -> MessageContext | None:
    """Lee el contexto del mensaje en un viaje; None = usar el camino anterior."""
    import dependencies
    from config import FORMALIZATION_DIAGNOSTICS_ENABLED, OPT_MESSAGE_CONTEXT_RPC

    if not OPT_MESSAGE_CONTEXT_RPC or not phone:
        return None
    client = getattr(dependencies, "supabase_admin", None)
    if client is None:
        return None

    params: dict[str, Any] = {"p_phone": phone, "p_include_formalization": FORMALIZATION_DIAGNOSTICS_ENABLED}
    if FORMALIZATION_DIAGNOSTICS_ENABLED:
        from db.formalization_diagnostics import _today

        # Mismo "hoy" (America/Santiago) que usa get_formalization_context.
        params["p_as_of"] = _today().isoformat()
    try:
        data = client.rpc(RPC_NAME, params).execute().data
    except Exception as error:
        # Sin detalle: los errores de PostgREST pueden incluir datos de la fila.
        logger.warning("No se pudo leer el contexto del mensaje (%s); se usa el camino anterior",
                       type(error).__name__)
        return None

    context = parse_message_context(data)
    if context is None:
        logger.warning("El contexto del mensaje llegó con un formato inesperado; se usa el camino anterior")
    return context
