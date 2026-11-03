import asyncio
import json
import logging
from collections import deque

import dependencies
from fastapi import APIRouter, HTTPException, Query, Request, Response

from config import META_WEBHOOK_VERIFY_TOKEN, DEBUG, OPT_DELIVERY_STATUS_RPC, OPT_REDIS_RATE_LIMIT
from core.expert_flow import EXPERT_RELAY
from core.roadmaps import extract_hito_context
from db.rate_limits import (
    RATE_LIMIT_WARNING,
    check_message_rate_limit,
    check_message_rate_limit_redis,
    is_rate_limit_exempt,
)
from db.calendar import update_calendar_delivery_status
from db.community_events import update_community_delivery_status
from db.delivery_status import DeliveryStatusUnavailable, apply_delivery_status
from db.expert_requests import get_open_expert_request
from db.message_context import MessageContext, get_message_context
from db.reminders import update_reminder_delivery_status
from db.users import (
    BLOCKED_USER_MESSAGE,
    get_last_user_message,
    get_user,
    is_user_blocked,
    set_audio_consent,
)
from phone_lock import acquire_phone_lock, release_phone_lock
from services.audio_consent import (
    ACCEPTED_AND_PROCESSING_TEXT,
    ACCEPTED_TEXT,
    DECLINED_REMINDER_TEXT,
    REJECTED_TEXT,
    WRONG_TEXT,
    audio_choice,
    consent_prompt,
    consent_state,
    echo_widget,
    pop_pending_audio,
    stash_pending_audio,
)
from services.message_router import (
    CONFIRM_PREFIX,
    CONFIRM_QUESTIONS,
    RESET_COMMANDS,
    route_message,
    split_message,
)
from services.pending_confirmation import (
    clear_pending_confirmation,
    get_pending_confirmation,
    is_affirmative,
    set_pending_confirmation,
)
from services.portal_auth import create_access_token
from services.performance import ai_queue_kwargs, financial_queue_kwargs, observe_webhook
from services.transcription import is_ambiguous_transcription, transcribe_audio
from services.whatsapp import (
    WhatsAppAPIError,
    download_media,
    normalize_phone,
    send_interactive_buttons,
    send_interactive_list,
    send_text,
    send_typing_indicator,
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


def _usable(context: MessageContext | None) -> bool:
    return context is not None and not context.stale and context.user is not None


def _has_open_expert_request(phone: str, context: MessageContext | None = None) -> bool:
    """¿Está eligiendo tema o conversando con un experto? En ese caso hay una
    persona del otro lado y no corresponde el filtro de audios ambiguos."""
    if _usable(context):
        return context.expert_request is not None
    try:
        user = get_user(phone)
        return bool(user and user.get("id") and get_open_expert_request(user["id"]))
    except Exception as error:
        logger.warning("No se pudo consultar la solicitud de experto: %s", error)
        return False


async def _flush_echo(phone: str, transcript: str | None) -> None:
    """Envía "Esto entendí de tu audio" como mensaje aparte y devuelve None."""
    if transcript:
        try:
            await _send_response(phone, echo_widget(transcript))
        except WhatsAppAPIError as error:
            logger.error("No se pudo mostrar la transcripción: %s", error)
    return None


def _audio_consent(phone: str, context: MessageContext | None = None) -> bool | None:
    """True/False si ya decidió sobre el procesamiento de audios; None si no."""
    if _usable(context):
        return consent_state(context.user)
    try:
        return consent_state(get_user(phone))
    except Exception as error:
        logger.warning("No se pudo consultar el consentimiento de audio: %s", error)
        return None


async def _apply_audio_choice(redis, phone: str, choice: str):
    """Aplica la respuesta del usuario sobre sus audios (HdU011, iteración).

    Devuelve (respuesta, media_id): media_id es el audio que quedó esperando el
    consentimiento y que hay que procesar ahora, si aceptó."""
    if choice == "accept":
        await asyncio.to_thread(set_audio_consent, phone, True)
        media_id = await pop_pending_audio(redis, phone)
        return (ACCEPTED_AND_PROCESSING_TEXT if media_id else ACCEPTED_TEXT), media_id
    if choice == "reject":
        await asyncio.to_thread(set_audio_consent, phone, False)
        await pop_pending_audio(redis, phone)
        return REJECTED_TEXT, None
    # "wrong": lo que se mostró no es lo que dijo. Se descarta cualquier
    # confirmación que haya quedado armada con esa mala interpretación.
    await clear_pending_confirmation(redis, phone)
    return WRONG_TEXT, None


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


async def _send_progress_notice(phone: str, text: str, message_id: str | None = None) -> None:
    """Aviso de que la respuesta está en camino. El job ya quedó encolado, así
    que un fallo del aviso solo se registra: no debe impedir la respuesta.

    Con `message_id` se muestran los tres puntos de "escribiendo…" en vez de un
    mensaje de texto (un mensaje menos en el chat). Si Meta rechaza el
    indicador, se envía el texto de siempre para que nunca quede en silencio."""
    if message_id:
        try:
            await send_typing_indicator(message_id)
            return
        except WhatsAppAPIError as error:
            logger.warning("No se pudo mostrar el indicador de escritura a %s: %s", phone, error)
    try:
        await send_text(phone, text)
    except WhatsAppAPIError as error:
        logger.error("No se pudo enviar el aviso de procesamiento a %s: %s", phone, error)


async def _apply_status(status: dict, failure_reason: str | None) -> None:
    """Actualiza el estado de entrega del wamid en la tabla que corresponda.

    Con OPT_DELIVERY_STATUS_RPC es una sola llamada (apply_delivery_status_v1).
    Si la RPC no está disponible, o el flag está apagado, se prueban las tablas
    una por una como antes.
    """
    if OPT_DELIVERY_STATUS_RPC:
        try:
            await asyncio.to_thread(
                apply_delivery_status,
                status.get("id", ""),
                status.get("status", ""),
                status.get("timestamp"),
                failure_reason,
            )
            return
        except DeliveryStatusUnavailable as error:
            logger.warning(
                "No se pudo aplicar el estado en una llamada (%s); se usa el camino anterior",
                error,
            )

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
    try:
        await asyncio.to_thread(
            update_community_delivery_status,
            status.get("id", ""),
            status.get("status", ""),
            status.get("timestamp"),
            failure_reason,
        )
    except Exception as error:
        logger.error(
            "No se pudo actualizar el estado del aviso de convocatoria %s: %s",
            status.get("id", "unknown"),
            error,
        )


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
@observe_webhook
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
                await _apply_status(status, failure_reason)

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
                    # Perfil y estados de conversación en una lectura (si está
                    # activo OPT_MESSAGE_CONTEXT_RPC). Se lee dentro del lock: si
                    # un job anterior de este teléfono seguía en curso, el
                    # contexto ya incluye lo que ese job guardó.
                    context = await asyncio.to_thread(get_message_context, phone)

                    # Usuario bloqueado manualmente (users.bloqueado): solo recibe
                    # el aviso de acceso, antes de transcribir audio para no
                    # gastar Whisper ni IA en él.
                    blocked = (
                        context.blocked if context is not None
                        else await asyncio.to_thread(is_user_blocked, phone)
                    )
                    if blocked:
                        logger.info("Mensaje ignorado: usuario bloqueado %s", phone)
                        try:
                            await send_text(phone, BLOCKED_USER_MESSAGE)
                        except WhatsAppAPIError as error:
                            logger.error("No se pudo avisar al usuario bloqueado: %s", error)
                        processed_messages += 1
                        continue

                    is_audio_message = incoming.get("type") == "audio"
                    message = _extract_message_text(incoming)
                    # Transcripción pendiente de mostrar. Si el audio termina en
                    # una respuesta de IA, viaja al worker y sale en el MISMO
                    # mensaje; en cualquier otro caso se envía antes de responder.
                    pending_echo = None

                    # ── Consentimiento de audios y "esto no es lo que dije" ──
                    choice = audio_choice(message)
                    if choice:
                        reply, pending_media_id = await _apply_audio_choice(redis, phone, choice)
                        if context is not None:
                            # Cambió el consentimiento: lo que sigue se relee.
                            context.stale = True
                        try:
                            if reply:
                                await _send_response(phone, reply)
                        except WhatsAppAPIError as error:
                            logger.error("No se pudo responder sobre el audio: %s", error)
                        if not pending_media_id:
                            processed_messages += 1
                            continue
                        # Aceptó mientras un audio esperaba: se procesa ahora,
                        # igual que si recién hubiera llegado.
                        incoming = {
                            "type": "audio",
                            "audio": {"id": pending_media_id},
                            "id": message_id,
                            "from": incoming.get("from"),
                        }
                        is_audio_message = True
                        message = None

                    if message is None and is_audio_message:
                        consent = await asyncio.to_thread(_audio_consent, phone, context)
                        if consent is not True:
                            # Sin consentimiento NO se transcribe: se pregunta
                            # (y el audio espera) o se recuerda que puede
                            # seguir por texto.
                            try:
                                if consent is None:
                                    media_id = (incoming.get("audio") or {}).get("id")
                                    if media_id:
                                        await stash_pending_audio(redis, phone, media_id)
                                    await _send_response(phone, consent_prompt())
                                else:
                                    await send_text(phone, DECLINED_REMINDER_TEXT)
                            except WhatsAppAPIError as error:
                                logger.error("No se pudo pedir el consentimiento de audio: %s", error)
                            processed_messages += 1
                            continue

                        message = await _transcribe_incoming_audio(incoming)
                        if message is not None and audio_choice(message) == "wrong":
                            # Lo dijo por voz ("no es lo que dije"): mismo efecto
                            # que el botón, sin mostrar el eco de esa frase.
                            reply, _ = await _apply_audio_choice(redis, phone, "wrong")
                            try:
                                await _send_response(phone, reply)
                            except WhatsAppAPIError as error:
                                logger.error("No se pudo responder sobre el audio: %s", error)
                            processed_messages += 1
                            continue
                        if message is not None:
                            # Siempre se muestra lo que se entendió, antes de
                            # responder o seguir con cualquier funcionalidad.
                            pending_echo = message

                    # ── Audio transcrito pero de confianza dudosa (HdU11, AC4) ──
                    # Se distingue de "message is None" (AC5): acá Whisper sí
                    # devolvió texto, pero muy corto o una frase de relleno
                    # típica de ruido/silencio — se le muestra al usuario lo
                    # que se entendió y se le pide repetir, sin tocar su perfil.
                    # El filtro no aplica cuando hay una persona o una pregunta
                    # esperando una respuesta corta: un "sí"/"no" dicho por audio
                    # como confirmación, o cualquier audio mientras el emprendedor
                    # elige tema o conversa con un experto (que sí lo interpreta).
                    skip_ambiguity_filter = bool(
                        message is not None
                        and is_audio_message
                        and (
                            await get_pending_confirmation(redis, phone)
                            or await asyncio.to_thread(_has_open_expert_request, phone, context)
                        )
                    )
                    if (
                        message is not None
                        and is_audio_message
                        and not skip_ambiguity_filter
                        and is_ambiguous_transcription(message)
                    ):
                        pending_echo = await _flush_echo(phone, pending_echo)
                        try:
                            await send_text(
                                phone,
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
                            user = (
                                context.user if _usable(context)
                                else await asyncio.to_thread(get_user, phone)
                            )
                            if user is None or user.get("onboarding_step") != "done":
                                onboarding_prompt = await asyncio.to_thread(
                                    route_message, phone, "", None, context=context,
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
                        pending_echo = await _flush_echo(phone, pending_echo)
                        await clear_pending_confirmation(redis, phone)
                        try:
                            if is_affirmative(message):
                                result = await asyncio.to_thread(
                                    route_message, phone, pending_action, None,
                                    context=context,
                                )
                                await _send_response(phone, result)
                            else:
                                await send_text(
                                    phone,
                                    "Entendido, no hice ningún cambio. Si quieres "
                                    "hacerlo más adelante, solo dímelo. 🙂",
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
                        pending_echo = await _flush_echo(phone, pending_echo)
                        try:
                            await set_pending_confirmation(redis, phone, "reiniciar")
                            await send_text(
                                phone,
                                "¿Confirmas que quieres *reiniciar tu perfil*? Vas a "
                                "perder tu progreso actual.\n\nResponde *sí* para "
                                "confirmar o *no* para cancelar.",
                            )
                        except WhatsAppAPIError as error:
                            logger.error("No se pudo pedir confirmación de reinicio: %s", error)
                        processed_messages += 1
                        continue

                    if not is_rate_limit_exempt(message):
                        if OPT_REDIS_RATE_LIMIT:
                            rate_limit = await check_message_rate_limit_redis(redis, phone)
                        else:
                            rate_limit = await asyncio.to_thread(
                                check_message_rate_limit,
                                phone,
                            )
                        if not rate_limit["allowed"]:
                            pending_echo = await _flush_echo(phone, pending_echo)
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
                        message_id=message_id,
                        context=context,
                        confirm_actions=is_audio_message,
                    )

                    try:
                        # Solo las consultas a la IA llevan la transcripción
                        # dentro de su respuesta; el resto la muestra antes.
                        if result not in ("__AI_QUERY__", "__AI_QUERY_WITH_CONTEXT__"):
                            pending_echo = await _flush_echo(phone, pending_echo)
                        ai_extra = {"audio_transcript": pending_echo} if pending_echo else {}

                        # Se encola antes de enviar el aviso: el worker empieza
                        # sin esperar a Meta. El lock por teléfono sigue
                        # garantizando que no se cruce con otra respuesta.
                        if result == "__AI_QUERY__":
                            await redis.enqueue_job(
                                "process_ai_task",
                                phone,
                                message,
                                lock_token=lock_token,
                                **ai_extra,
                                **ai_queue_kwargs(),
                            )
                            hand_off_to_worker = True
                            await _send_progress_notice(phone, "🤔 Déjame pensar tu respuesta...", message_id)

                        elif result == "__FINANCIAL_PARSE__":
                            await redis.enqueue_job(
                                "process_financial_movement_task",
                                phone,
                                message,
                                lock_token=lock_token,
                                **financial_queue_kwargs(),
                            )
                            hand_off_to_worker = True
                            await _send_progress_notice(phone, "🧾 Estoy revisando el movimiento...", message_id)

                        elif result == "__AI_QUERY_WITH_CONTEXT__":
                            user = (
                                context.user if _usable(context)
                                else await asyncio.to_thread(get_user, phone)
                            )
                            hito_context = extract_hito_context(user) if user else None
                            await redis.enqueue_job(
                                "process_ai_task",
                                phone,
                                message,
                                hito_context=hito_context,
                                reformulate_mode=False,
                                lock_token=lock_token,
                                **ai_extra,
                                **ai_queue_kwargs(),
                            )
                            hand_off_to_worker = True
                            await _send_progress_notice(phone, "🤔 Te ayudo con este hito...", message_id)

                        elif isinstance(result, str) and result.startswith(CONFIRM_PREFIX):
                            # Acción que cambia datos, pedida por audio: se le
                            # muestra lo entendido y se espera un sí/no.
                            command = result[len(CONFIRM_PREFIX):]
                            await set_pending_confirmation(redis, phone, command)
                            await send_text(
                                phone,
                                f"¿Confirmas que quieres {CONFIRM_QUESTIONS.get(command, command)}?\n\n"
                                "Responde *sí* para confirmar o *no* para cancelar.",
                            )

                        elif result == EXPERT_RELAY:
                            # Mensaje reenviado al panel del experto (HdU16):
                            # no hay respuesta automática que mandar.
                            logger.info("Mensaje de %s reenviado al experto", phone)

                        elif result in ("__WEB_PANEL_LINK__", "__DIAGNOSTIC_PANEL_LINK__"):
                            token = await create_access_token(redis, phone)
                            link = f"{str(request.base_url).rstrip('/')}/portal/acceso?token={token}"
                            diagnostic_link = result == "__DIAGNOSTIC_PANEL_LINK__"
                            if diagnostic_link:
                                link += "&tab=diagnostico"
                            await send_text(
                                phone,
                                (f"📊 *Tu diagnóstico en el panel*\n\n🔗 Abre este enlace desde tu celular:\n{link}\n\n"
                                 "✏️ Ajusta los costos que necesites.\n✅ Marca la revisión y pulsa «Finalizar diagnóstico».\n\n"
                                 "💰 Tus ingresos y gastos los seguimos revisando por WhatsApp.\n\n"
                                 if diagnostic_link else f"📊 Acá está el link a tu panel: {link}\n\n") +
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
                                await redis.enqueue_job(
                                    "process_ai_task",
                                    phone,
                                    last_message,
                                    hito_context=None,
                                    reformulate_mode=True,
                                    lock_token=lock_token,
                                    **ai_queue_kwargs(),
                                )
                                hand_off_to_worker = True
                                await _send_progress_notice(
                                    phone,
                                    "Tienes razón, déjame explicarlo de otra forma...",
                                )

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
