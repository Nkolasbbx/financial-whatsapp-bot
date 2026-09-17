"""Orquestación asíncrona de extracción financiera y respuesta por WhatsApp."""

from __future__ import annotations

import asyncio
import logging

from config import TWILIO_WHATSAPP_NUMBER
from core.financial_flow import confirmation_response, missing_data_response
from core.financial_parser import extract_financial_movement
from db.financial_movements import (
    get_financial_session,
    start_financial_session,
)
from db.users import get_user
from schemas.financial_movements import FinancialMovementConfirm
from services.whatsapp import (
    WhatsAppAPIError,
    send_interactive_buttons,
    send_text,
)


logger = logging.getLogger("financial")


async def _send_widget(phone: str, widget: dict | str) -> None:
    if isinstance(widget, str):
        await send_text(phone, widget)
        return
    if widget.get("type") == "buttons":
        await send_interactive_buttons(
            phone,
            widget.get("body", ""),
            widget.get("options") or [],
        )
        return
    await send_text(phone, widget.get("body", ""))


async def prepare_financial_movement(
    phone: str,
    message: str,
) -> dict | str:
    """Interpreta el mensaje y retorna la siguiente respuesta conversacional.

    Esta función nunca inserta directamente en ``financial_movements``. Solo
    deja una sesión confirmable; la escritura definitiva ocurre cuando el
    usuario presiona el botón ``finance_confirm``.
    """
    user = await asyncio.to_thread(get_user, phone)
    if not user or not user.get("id"):
        return (
            "No pude identificar tu perfil para registrar el movimiento."
        )

    user_id = user["id"]
    session = await asyncio.to_thread(get_financial_session, user_id)
    existing_draft = dict((session or {}).get("draft") or {})
    target_movement_id = (session or {}).get("target_movement_id")

    extraction = await asyncio.to_thread(
        extract_financial_movement,
        message,
        existing_draft,
    )
    draft = extraction.draft_payload()
    missing_fields = extraction.required_missing_fields()

    if missing_fields:
        await asyncio.to_thread(
            start_financial_session,
            user_id,
            "waiting_missing_data",
            draft=draft,
            missing_fields=missing_fields,
            target_movement_id=target_movement_id,
        )
        return missing_data_response(extraction)

    movement = FinancialMovementConfirm(
        **draft,
        target_movement_id=target_movement_id,
    )
    state = "confirming_update" if target_movement_id else "confirming_creation"
    await asyncio.to_thread(
        start_financial_session,
        user_id,
        state,
        draft=movement.model_dump(
            mode="json",
            exclude={"target_movement_id"},
            exclude_none=True,
        ),
        missing_fields=[],
        target_movement_id=target_movement_id,
    )

    return confirmation_response(
        movement,
        editing=target_movement_id is not None,
    )


async def process_financial_movement_and_send(
    phone: str,
    message: str,
) -> None:
    """Procesa el movimiento y envía la respuesta con Meta WhatsApp."""
    widget = await prepare_financial_movement(phone, message)
    try:
        await _send_widget(phone, widget)
        logger.info("Movimiento financiero listo para confirmar para %s", phone)
    except WhatsAppAPIError as error:
        logger.error(
            "No se pudo enviar la confirmación financiera a %s: %s",
            phone,
            error,
        )
        raise


async def process_financial_movement_and_send_twilio(
    phone_whatsapp: str,
    phone_clean: str,
    message: str,
    twilio_client,
) -> None:
    """Fallback textual para el modo DEBUG que todavía utiliza Twilio."""
    if twilio_client is None:
        logger.error("Twilio no está configurado para responder a %s", phone_clean)
        return

    widget = await prepare_financial_movement(phone_clean, message)
    if isinstance(widget, str):
        body = widget
    else:
        body = widget.get("body", "")
        options = widget.get("options") or []
        if options:
            option_lines = "\n".join(f"• {title}" for _, title in options)
            body = f"{body}\n\nResponde con una opción:\n{option_lines}"

    await asyncio.to_thread(
        twilio_client.messages.create,
        body=body,
        from_=TWILIO_WHATSAPP_NUMBER,
        to=phone_whatsapp,
    )
