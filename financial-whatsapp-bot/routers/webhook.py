import asyncio
import json
import logging
from collections import deque

import dependencies
from fastapi import APIRouter, HTTPException, Query, Request, Response

from config import META_WEBHOOK_VERIFY_TOKEN, DEBUG
from core.roadmaps import extract_hito_context
from db.rate_limits import (
    RATE_LIMIT_WARNING,
    check_message_rate_limit,
    is_rate_limit_exempt,
)
from db.calendar import update_calendar_delivery_status
from db.reminders import update_reminder_delivery_status
from db.users import get_last_user_message, get_user
from phone_lock import acquire_phone_lock, release_phone_lock
from services.message_router import RESET_COMMANDS, route_message, split_message
from services.pending_confirmation import (
    clear_pending_confirmation,
    get_pending_confirmation,
    is_affirmative,
    set_pending_confirmation,
)
from services.portal_auth import create_access_token
from services.transcription import is_ambiguous_transcription, transcribe_audio
from services.whatsapp import (
    WhatsAppAPIError,
    download_media,
    normalize_phone,
    send_interactive_buttons,
    send_interactive_list,
    send_text,
    verify_webhook_signature,
)

logger = logging.getLogger("financial")

router = APIRouter()

_MESSAGE_ID_TTL_SECONDS = 86400  # 24h es de sobra para descartar duplicados de Meta


async def _remember_message(redis, message_id: str) -> bool:
    """Registra un mensaje en Redis y devuelve False si Meta ya lo había enviado."""
    if not message_id:
        return True
    # SET NX: solo escribe si la key no existe. Es atómico, así que no hay
    # condición de carrera aunque lleguen dos webhooks casi simultáneos.
    was_set = await redis.set(
        f"msg_seen:{message_id}", "1", nx=True, ex=_MESSAGE_ID_TTL_SECONDS
    )
    return bool(was_set)


_TRAILING_PUNCTUATION = " .,!?¡¿;:\"'"


def _normalize_audio_command(text: str) -> str:
    """Normaliza texto transcrito para comparar contra comandos exactos.

    Whisper suele agregar puntuación/mayúsculas de más al transcribir
    habla real (ej. "Reiniciar." en vez de "reiniciar") — a diferencia de
    un mensaje escrito, donde el usuario ya lo tipea limpio. Sin esto, un
    "reiniciar" dicho en voz alta rara vez calza exacto con RESET_COMMANDS.
    """
    return text.strip().lower().strip(_TRAILING_PUNCTUATION)


def _extract_message_text(incoming: dict) -> str | None:
    message_type = incoming.get("type")
    if message_type == "text":
        return incoming.get("text", {}).get("body", "").strip() or None
    if message_type == "button":
        button = incoming.get("button", {})
        return (button.get("text") or button.get("payload") or "").strip() or None
    if message_type == "interactive":
        interactive = incoming.get("interactive", {})
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        return (reply.get("id") or reply.get("title") or "").strip() or None
    return None


async def _transcribe_incoming_audio(incoming: dict) -> str | None:
    """Prototipo de factibilidad (HdU01): intenta transcribir una nota de
    voz a texto. Nunca lanza — cualquier fallo (descarga o transcripción)
    devuelve None y el llamador cae al camino existente ("no puedo
    procesar esto")."""
    media_id = incoming.get("audio", {}).get("id")
    if not media_id:
        return None

    try:
        audio_bytes, mime_type = await download_media(media_id)
    except WhatsAppAPIError as error:
        logger.error("No se pudo descargar la nota de voz: %s", error)
        return None

    return await transcribe_audio(audio_bytes, mime_type)


async def _send_response(phone: str, result) -> None:
    if isinstance(result, dict):
        result_type = result.get("type", "text")
        body = result.get("body", "")

        if result_type == "buttons":
            await send_interactive_buttons(phone, body, result["options"])
            return
        if result_type == "list":
            await send_interactive_list(
                phone,
                body,
                result.get("button_text", "Elegir"),
                result["options"],
            )
            return

        for part in split_message(body, 4000):
            await send_text(phone, part)
        return

    for part in split_message(result, 4000):
        await send_text(phone, part)


@router.get("/webhook/whatsapp")
async def verify_whatsapp_webhook(
    mode: str | None = Query(default=None, alias="hub.mode"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
):
    if (
        mode == "subscribe"
        and META_WEBHOOK_VERIFY_TOKEN
        and token == META_WEBHOOK_VERIFY_TOKEN
        and challenge is not None
    ):
        logger.info("Webhook de Meta verificado correctamente")
        return Response(content=challenge, media_type="text/plain")

    raise HTTPException(status_code=403, detail="Token de verificación inválido")


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    """Recibe mensajes y estados de WhatsApp Cloud API. Todo el trabajo pesado
    se encola en Redis; este endpoint solo valida, parsea y encola."""
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not verify_webhook_signature(raw_body, signature):
        logger.warning("Webhook de Meta rechazado por firma inválida")
        raise HTTPException(status_code=401, detail="Firma de webhook inválida")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="JSON inválido") from error

    if payload.get("object") != "whatsapp_business_account":
        return {"status": "ignored"}

    redis = request.app.state.redis
    processed_messages = 0

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            for status in value.get("statuses", []):
                logger.info(
                    "Estado WhatsApp %s para %s",
                    status.get("status", "unknown"),
                    status.get("id", "unknown"),
                )
                errors = status.get("errors") or []
                failure_reason = json.dumps(errors, ensure_ascii=False) if errors else None
                try:
                    await asyncio.to_thread(
                        update_reminder_delivery_status,
                        status.get("id", ""),
                        status.get("status", ""),
                        status.get("timestamp"),
                        failure_reason,
                    )
                except Exception as error:
                    logger.error(
                        "No se pudo actualizar el estado del recordatorio %s: %s",
                        status.get("id", "unknown"),
                        error,
                    )
                try:
                    await asyncio.to_thread(
                        update_calendar_delivery_status,
                        status.get("id", ""),
                        status.get("status", ""),
                        status.get("timestamp"),
                        failure_reason,
                    )
                except Exception as error:
                    logger.error(
                        "No se pudo actualizar el estado del calendario %s: %s",
                        status.get("id", "unknown"),
                        error,
                    )

            for incoming in value.get("messages", []):
                message_id = incoming.get("id", "")
                if not await _remember_message(redis, message_id):
                    logger.info("Webhook duplicado ignorado: %s", message_id)
                    continue

                phone = normalize_phone(incoming.get("from", ""))
                if not phone:
                    logger.warning("Mensaje de Meta sin teléfono válido")
                    continue

                # Lock distribuido en Redis: garantiza que, para este teléfono,
                # no se envíe ninguna otra respuesta mientras un job de IA
                # encolado por un mensaje anterior siga en curso en el worker.
                # Si el mensaje termina encolándose (__AI_QUERY__ y variantes),
                # NO se libera acá: se le pasa el token al job y es
                # process_ai_task quien lo libera al terminar (ver worker.py).
                lock_token = await acquire_phone_lock(redis, phone)
                hand_off_to_worker = False
                try:
                    is_audio_message = incoming.get("type") == "audio"
                    message = _extract_message_text(incoming)

                    if message is None and is_audio_message:
                        message = await _transcribe_incoming_audio(incoming)

                    # ── Audio transcrito pero de confianza dudosa (HdU11, AC4) ──
                    # Se distingue de "message is None" (AC5): acá Whisper sí
                    # devolvió texto, pero muy corto o una frase de relleno
                    # típica de ruido/silencio — se le muestra al usuario lo
                    # que se entendió y se le pide repetir, sin tocar su perfil.
                    if (
                        message is not None
                        and is_audio_message
                        and is_ambiguous_transcription(message)
                    ):
                        try:
                            await send_text(
                                phone,
                                f'🎤 Escuché: "{message}"\n\n'
                                "No estoy seguro de haber entendido bien. ¿Puedes "
                                "repetir el audio más claro, o escribirme tu "
                                "consulta por texto?",
                            )
                        except WhatsAppAPIError as error:
                            logger.error("No se pudo responder al audio ambiguo: %s", error)
                        processed_messages += 1
                        continue

                    if message is None:
                        try:
                            if is_audio_message:
                                # La nota de voz llegó, pero falló la descarga o la
                                # transcripción (vacía, sin voz comprensible, error
                                # de red) — HdU11, AC5: se informa el problema
                                # puntual, sin avanzar el flujo ni tocar datos.
                                await send_text(
                                    phone,
                                    "🎤 No pude entender tu nota de voz — puede que "
                                    "esté vacía, tenga mucho ruido o no se escuche "
                                    "bien. ¿Puedes intentar grabarla de nuevo en un "
                                    "lugar más silencioso, o escribirme tu consulta "
                                    "por texto?",
                                )
                            else:
                                await send_text(
                                    phone,
                                    "Por ahora solo puedo procesar mensajes de texto, "
                                    "notas de voz o botones. Escríbeme tu consulta y "
                                    "te ayudo 😊",
                                )
                            # Si el usuario todavía está en onboarding, no basta con
                            # avisar: hay que repetir la pregunta del paso actual sin
                            # perder el progreso (HdU01). route_message con mensaje
                            # vacío no matchea ninguna opción válida del paso, así que
                            # process_onboarding re-muestra la misma pregunta tal como
                            # ya hace ante cualquier respuesta no reconocida.
                            user = await asyncio.to_thread(get_user, phone)
                            if user is None or user.get("onboarding_step") != "done":
                                onboarding_prompt = await asyncio.to_thread(
                                    route_message, phone, "", None
                                )
                                await _send_response(phone, onboarding_prompt)
                        except WhatsAppAPIError as error:
                            logger.error("No se pudo responder al mensaje no textual: %s", error)
                        processed_messages += 1
                        continue

                    # ── Confirmación pendiente de un comando destructivo (HdU11, AC3) ──
                    # Solo existe pending_confirm si un audio anterior pidió
                    # reiniciar el perfil (ver más abajo); un "sí"/"no" acá
                    # resuelve esa confirmación antes de seguir el flujo normal.
                    pending_action = await get_pending_confirmation(redis, phone)
                    if pending_action:
                        await clear_pending_confirmation(redis, phone)
                        try:
                            if is_affirmative(message):
                                result = await asyncio.to_thread(
                                    route_message, phone, pending_action, None
                                )
                                await _send_response(phone, result)
                            else:
                                await send_text(
                                    phone,
                                    "Entendido, no hice ningún cambio. Si quieres "
                                    "reiniciar más adelante, solo dímelo. 🙂",
                                )
                        except WhatsAppAPIError as error:
                            logger.error("No se pudo resolver la confirmación pendiente: %s", error)
                        processed_messages += 1
                        continue

                    # ── Comando destructivo llegado por audio (HdU11, AC3) ──
                    # Un "reiniciar" escrito o tocado como botón sigue siendo una
                    # acción explícita del usuario y se ejecuta directo (ver
                    # services/message_router.py). Por audio, en cambio, se
                    # confirma primero: se le muestra lo entendido y se espera
                    # un sí/no antes de tocar sus datos.
                    if is_audio_message and _normalize_audio_command(message) in RESET_COMMANDS:
                        try:
                            await set_pending_confirmation(redis, phone, "reiniciar")
                            await send_text(
                                phone,
                                f'🎤 Escuché: "{message}"\n\n'
                                "¿Confirmas que quieres *reiniciar tu perfil*? Vas a "
                                "perder tu progreso actual.\n\nResponde *sí* para "
                                "confirmar o *no* para cancelar.",
                            )
                        except WhatsAppAPIError as error:
                            logger.error("No se pudo pedir confirmación de reinicio: %s", error)
                        processed_messages += 1
                        continue

                    if not is_rate_limit_exempt(message):
                        rate_limit = await asyncio.to_thread(
                            check_message_rate_limit,
                            phone,
                        )
                        if not rate_limit["allowed"]:
                            logger.warning(
                                "Mensaje bloqueado por rate limit: phone=%s "
                                "retry_after=%s",
                                phone,
                                rate_limit["retry_after_seconds"],
                            )
                            if rate_limit["notify_user"]:
                                try:
                                    await send_text(phone, RATE_LIMIT_WARNING)
                                except WhatsAppAPIError as error:
                                    logger.error(
                                        "No se pudo notificar el rate limit a %s: %s",
                                        phone,
                                        error,
                                    )
                            processed_messages += 1
                            continue

                    logger.info("Mensaje de WhatsApp recibido desde %s", phone)
                    reply_to_message_id = incoming.get("context", {}).get("id")

                    # route_message decide QUÉ tipo de tarea es (rápida vs IA),
                    # pero ya no ejecuta la IA aquí: solo clasifica.
                    result = await asyncio.to_thread(
                        route_message,
                        phone,
                        message,
                        reply_to_message_id,
                    )

                    try:
                        if result == "__AI_QUERY__":
                            await send_text(phone, "🤔 Déjame pensar tu respuesta...")
                            await redis.enqueue_job(
                                "process_ai_task",
                                phone,
                                message,
                                lock_token=lock_token,
                            )
                            hand_off_to_worker = True

                        elif result == "__AI_QUERY_WITH_CONTEXT__":
                            await send_text(phone, "🤔 Te ayudo con este hito...")
                            user = await asyncio.to_thread(get_user, phone)
                            hito_context = extract_hito_context(user) if user else None
                            await redis.enqueue_job(
                                "process_ai_task",
                                phone,
                                message,
                                hito_context=hito_context,
                                reformulate_mode=False,
                                lock_token=lock_token,
                            )
                            hand_off_to_worker = True

                        elif result == "__WEB_PANEL_LINK__":
                            token = await create_access_token(redis, phone)
                            link = f"{str(request.base_url).rstrip('/')}/portal/acceso?token={token}"
                            await send_text(
                                phone,
                                f"📊 Acá está el link a tu panel: {link}\n\n"
                                "Es de un solo uso y vence en 30 minutos — si "
                                "se vence, vuelve a pedirlo desde el menú.",
                            )

                        elif result == "__AI_QUERY_WITH_REFORMULATE__":
                            last_message = await asyncio.to_thread(get_last_user_message, phone)

                            if not last_message:
                                await send_text(
                                    phone,
                                    "No pude encontrar tu pregunta anterior. "
                                    "¿Puedes escribirla nuevamente?",
                                )
                            else:
                                await send_text(
                                    phone,
                                    "Tienes razón, déjame explicarlo de otra forma...",
                                )
                                await redis.enqueue_job(
                                    "process_ai_task",
                                    phone,
                                    last_message,
                                    hito_context=None,
                                    reformulate_mode=True,
                                    lock_token=lock_token,
                                )
                                hand_off_to_worker = True

                        else:
                            await _send_response(phone, result)
                            logger.info("Respuesta enviada a %s", phone)
                    except WhatsAppAPIError as error:
                        logger.error("No se pudo enviar la respuesta a %s: %s", phone, error)

                    processed_messages += 1
                finally:
                    if not hand_off_to_worker:
                        await release_phone_lock(redis, phone, lock_token)

    return {"status": "received", "processed_messages": processed_messages}
