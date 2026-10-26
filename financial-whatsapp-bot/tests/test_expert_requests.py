import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import expert_flow
from routers import admin_solicitudes as panel
from routers import webhook

RESET = {"reiniciar", "menu_reiniciar"}
USER = {"id": "user-1", "comuna": "Recoleta"}
NAMES = (
    "get_open_expert_request", "create_expert_request", "set_expert_topic",
    "finalize_expert_request", "delete_expert_draft", "add_expert_message",
    "touch_user_message", "resolve_expert_request", "get_last_expert_message",
)


def _iso(delta):
    return (datetime.now(timezone.utc) + delta).isoformat()


def _flow(message, open_request=None, user=USER, finalize=True):
    patches = {name: patch.object(expert_flow, name) for name in NAMES}
    mocks = {name: p.start() for name, p in patches.items()}
    mocks["get_open_expert_request"].return_value = open_request
    mocks["create_expert_request"].return_value = {"id": "r1"}
    mocks["finalize_expert_request"].return_value = finalize
    # Por defecto el último mensaje es del emprendedor, reciente: sin acuse de recibo.
    mocks["get_last_expert_message"].return_value = {"sender": "user", "created_at": _iso(timedelta(minutes=-1))}
    try:
        result = expert_flow.handle_expert_message(user, message, RESET)
    finally:
        for p in patches.values():
            p.stop()
    return result, mocks


def _draft(topic=None, age=timedelta(minutes=-1)):
    return {"id": "r1", "status": "draft", "topic": topic, "created_at": _iso(age)}


class EntryAndDraftTests(unittest.TestCase):
    def test_entry_creates_a_draft_and_asks_for_the_topic(self):
        result, m = _flow("menu_experto")
        m["create_expert_request"].assert_called_once_with("user-1", "Recoleta", status="draft")
        self.assertEqual(result["type"], "list")
        ids = [option_id for option_id, _ in result["options"]]
        self.assertIn("experto_tema:patentes", ids)
        self.assertIn("experto_cancelar", ids)

    def test_natural_phrase_also_starts_the_flow(self):
        _, m = _flow("quiero hablar con un experto por favor")
        m["create_expert_request"].assert_called_once()

    def test_comuna_without_admin_is_told_it_is_not_available(self):
        result, m = _flow("menu_experto", user={"id": "u", "comuna": "Providencia"})
        m["create_expert_request"].assert_not_called()
        self.assertIn("solo para Recoleta y El Bosque", result)

    def test_topic_choice_is_saved_and_detail_is_asked(self):
        result, m = _flow("experto_tema:patentes", open_request=_draft())
        m["set_expert_topic"].assert_called_once_with("r1", "patentes")
        self.assertEqual(result["type"], "buttons")
        self.assertIn("Patentes", result["body"])

    def test_invalid_topic_shows_the_list_again(self):
        result, m = _flow("hola", open_request=_draft())
        m["set_expert_topic"].assert_not_called()
        self.assertEqual(result["type"], "list")

    def test_free_text_becomes_the_reason_and_publishes_the_request(self):
        result, m = _flow("No sé cómo pagar la patente", open_request=_draft(topic="patentes"))
        m["finalize_expert_request"].assert_called_once_with("r1", "No sé cómo pagar la patente")
        self.assertIn("dentro de 24 horas", result)
        self.assertIn("Patentes", result)

    def test_skip_publishes_without_reason(self):
        _, m = _flow("experto_omitir", open_request=_draft(topic="sii"))
        m["finalize_expert_request"].assert_called_once_with("r1", None)

    def test_reason_is_capped(self):
        _, m = _flow("x" * 1000, open_request=_draft(topic="otro"))
        self.assertEqual(len(m["finalize_expert_request"].call_args.args[1]), expert_flow.MAX_REASON_CHARS)

    def test_cancel_discards_the_draft(self):
        result, m = _flow("experto_cancelar", open_request=_draft(topic="sii"))
        m["delete_expert_draft"].assert_called_once_with("r1")
        self.assertIn("cancelé", result)

    def test_reset_or_menu_while_drafting_discards_it_and_continues(self):
        for word in ("reiniciar", "menu"):
            result, m = _flow(word, open_request=_draft(topic="sii"))
            m["delete_expert_draft"].assert_called_once_with("r1")
            self.assertIsNone(result)

    def test_stale_draft_is_dropped_and_a_new_one_starts(self):
        result, m = _flow("menu_experto", open_request=_draft(age=timedelta(hours=-2)))
        m["delete_expert_draft"].assert_called_once_with("r1")
        m["create_expert_request"].assert_called_once()
        self.assertEqual(result["type"], "list")


class OpenRequestTests(unittest.TestCase):
    def test_second_request_is_not_duplicated(self):
        result, m = _flow("menu_experto", open_request={"id": "r1", "status": "pending"})
        m["create_expert_request"].assert_not_called()
        self.assertIn("solicitud en curso", result)

    def test_pending_request_leaves_normal_assistant_running(self):
        result, _ = _flow("¿qué es el IVA?", open_request={"id": "r1", "status": "pending"})
        self.assertIsNone(result)

    def test_unrelated_message_without_request_is_ignored(self):
        result, m = _flow("hola")
        self.assertIsNone(result)
        m["create_expert_request"].assert_not_called()

    def test_in_progress_relays_message_and_pauses_ai(self):
        result, m = _flow("gracias", open_request={"id": "r1", "status": "in_progress"})
        self.assertEqual(result, expert_flow.EXPERT_RELAY)
        m["add_expert_message"].assert_called_once_with("r1", "user", "gracias", wa_message_id=None)
        m["touch_user_message"].assert_called_once_with("r1")

    def test_relayed_message_keeps_its_whatsapp_id_so_it_can_be_quoted(self):
        with patch.object(expert_flow, "get_open_expert_request", return_value={"id": "r1", "status": "in_progress"}),              patch.object(expert_flow, "get_last_expert_message", return_value=None),              patch.object(expert_flow, "add_expert_message") as add,              patch.object(expert_flow, "touch_user_message"):
            expert_flow.handle_expert_message(USER, "hola", RESET, "wamid.ABC")
        add.assert_called_once_with("r1", "user", "hola", wa_message_id="wamid.ABC")

    def test_user_can_end_conversation(self):
        result, m = _flow("Terminar", open_request={"id": "r1", "status": "in_progress"})
        m["resolve_expert_request"].assert_called_once_with("r1", "emprendedor")
        m["add_expert_message"].assert_not_called()
        self.assertIn("Terminó la conversación", result)

    def test_reset_closes_request_and_continues_to_reset(self):
        result, m = _flow("reiniciar", open_request={"id": "r1", "status": "in_progress"})
        m["resolve_expert_request"].assert_called_once()
        self.assertIsNone(result)

    def test_database_error_falls_back_to_normal_flow(self):
        with patch.object(expert_flow, "get_open_expert_request", side_effect=RuntimeError("db")):
            self.assertIsNone(expert_flow.handle_expert_message(USER, "menu_experto", RESET))

    def test_user_without_id_is_ignored(self):
        result, _ = _flow("menu_experto", user={"comuna": "Recoleta"})
        self.assertIsNone(result)


def _row(status="in_progress", last=timedelta(hours=-1), **extra):
    return {
        "id": "abcd1234-0000", "status": status, "user_last_message_at": _iso(last),
        "created_at": _iso(timedelta(hours=-2)), "topic": "patentes",
        "reason": "No sé cómo pagar la patente",
        "users": {
            "phone": "+56911112222", "rubro": "alimentos", "inicio_sii": "si",
            "roadmap": [{"title": "A", "done": True}, {"title": "B", "done": False}],
        },
        **extra,
    }


def _request():
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(redis=object())), query_params={},
    )


class WindowTests(unittest.TestCase):
    def test_window_is_24h_from_last_user_message(self):
        restante = panel.window_remaining(_row(last=timedelta(hours=-1)))
        self.assertAlmostEqual(restante.total_seconds(), 23 * 3600, delta=5)

    def test_expired_window_is_zero(self):
        self.assertEqual(panel.window_remaining(_row(last=timedelta(hours=-30))), timedelta(0))
        self.assertEqual(panel._window_text(timedelta(0)), "Ventana cerrada")

    def test_parses_postgres_timestamps_with_short_fraction(self):
        self.assertIsNotNone(panel._parse_dt("2026-10-11T12:00:00.12345+00:00"))
        self.assertIsNotNone(panel._parse_dt("2026-10-11T12:00:00Z"))


class PrioritizationTests(unittest.TestCase):
    def test_closest_to_closing_window_comes_first_and_closed_last(self):
        calma = _row(id="calma", last=timedelta(hours=-1))
        urgente = _row(id="urgente", last=timedelta(hours=-21))
        cerrada = _row(id="cerrada", last=timedelta(hours=-30))
        orden = [f["id"] for f in panel.ordenar_por_urgencia([calma, cerrada, urgente])]
        self.assertEqual(orden, ["urgente", "calma", "cerrada"])

    def test_filter_by_topic_rubro_and_formalization(self):
        a = _row(id="a", topic="patentes")
        b = _row(id="b", topic="sii")
        b["users"] = {**b["users"], "rubro": "textil", "inicio_sii": "no"}
        self.assertEqual([f["id"] for f in panel.filtrar([a, b], tema="sii")], ["b"])
        self.assertEqual([f["id"] for f in panel.filtrar([a, b], rubro="alimentos")], ["a"])
        self.assertEqual([f["id"] for f in panel.filtrar([a, b], formalizado="si")], ["a"])
        self.assertEqual([f["id"] for f in panel.filtrar([a, b], formalizado="no")], ["b"])

    def test_filter_window_about_to_close(self):
        urgente = _row(id="u", last=timedelta(hours=-20))
        calma = _row(id="c", last=timedelta(hours=-1))
        cerrada = _row(id="x", last=timedelta(hours=-30))
        ids = [f["id"] for f in panel.filtrar([urgente, calma, cerrada], vence=True)]
        self.assertEqual(ids, ["u"])


class AdminPanelTests(unittest.IsolatedAsyncioTestCase):
    def _patch(self, row):
        return [
            patch.object(panel, "_auth", new=AsyncMock(return_value={"comuna": "Recoleta", "nombre": "InnovaRecoleta"})),
            patch.object(panel, "get_expert_request", return_value=row),
            patch.object(panel, "_set_flash", new=AsyncMock()),
            patch.object(panel, "_pop_flash", new=AsyncMock(return_value=None)),
            patch.object(panel, "list_expert_messages", return_value=[]),
        ]

    async def _with(self, row, coro_fn, **extra_patches):
        patches = self._patch(row)
        for p in patches:
            p.start()
        try:
            return await coro_fn()
        finally:
            for p in patches:
                p.stop()

    async def test_detail_page_never_shows_the_phone_number(self):
        response = await self._with(_row(), lambda: panel.detalle("abcd1234-0000", _request(), "sid"))
        self.assertNotIn(b"56911112222", response.body)

    async def test_detail_shows_topic_reason_and_context(self):
        response = await self._with(_row(), lambda: panel.detalle("abcd1234-0000", _request(), "sid"))
        body = response.body.decode()
        self.assertIn("Patentes", body)
        self.assertIn("No sé cómo pagar la patente", body)
        self.assertIn("Formalizado", body)
        self.assertIn("1 de 2 hitos", body)

    async def test_reason_is_html_escaped(self):
        row = _row(reason="<script>alert(1)</script>")
        response = await self._with(row, lambda: panel.detalle("abcd1234-0000", _request(), "sid"))
        self.assertNotIn(b"<script>alert(1)", response.body)

    async def test_list_applies_filters_from_the_query_string(self):
        request = _request()
        request.query_params = {"tema": "sii"}
        a, b = _row(id="aaaa1111", topic="patentes"), _row(id="bbbb2222", topic="sii")
        with patch.object(panel, "_auth", new=AsyncMock(return_value={"comuna": "Recoleta", "nombre": "x"})), \
             patch.object(panel, "_pop_flash", new=AsyncMock(return_value=None)), \
             patch.object(panel, "list_expert_requests", side_effect=lambda c, st, limit=50: [a, b] if st == ("pending",) else []):
            response = await panel.lista(request, "sid")
        body = response.body.decode()
        self.assertIn("BBBB", body)
        self.assertNotIn("AAAA", body)

    async def test_take_notifies_user_and_marks_in_progress(self):
        async def run():
            with patch.object(panel, "take_expert_request", return_value=True) as take, \
                 patch.object(panel, "send_text", new=AsyncMock()) as send:
                response = await panel.tomar("r1", _request(), "sid")
            return response, take, send
        response, take, send = await self._with(_row("pending"), run)
        take.assert_called_once()
        self.assertEqual(send.await_args.args[0], "+56911112222")
        self.assertEqual(response.status_code, 303)

    async def test_take_is_refused_when_window_closed(self):
        async def run():
            with patch.object(panel, "take_expert_request") as take:
                await panel.tomar("r1", _request(), "sid")
            return take
        take = await self._with(_row("pending", last=timedelta(hours=-30)), run)
        take.assert_not_called()

    async def test_message_is_sent_and_saved_inside_window(self):
        async def run():
            with patch.object(panel, "send_text", new=AsyncMock()) as send, \
                 patch.object(panel, "add_expert_message") as add:
                await panel.enviar_mensaje("r1", _request(), mensaje="Hola, ¿en qué te ayudo?", financial_admin_session="sid")
            return send, add
        send, add = await self._with(_row(), run)
        self.assertIn("InnovaRecoleta", send.await_args.args[1])
        add.assert_called_once_with("r1", "expert", "Hola, ¿en qué te ayudo?", reply_to_id=None)

    async def test_message_is_blocked_outside_window(self):
        async def run():
            with patch.object(panel, "send_text", new=AsyncMock()) as send, \
                 patch.object(panel, "add_expert_message") as add:
                await panel.enviar_mensaje("r1", _request(), mensaje="Hola", financial_admin_session="sid")
            return send, add
        send, add = await self._with(_row(last=timedelta(hours=-30)), run)
        send.assert_not_awaited()
        add.assert_not_called()

    async def test_request_from_another_comuna_is_404(self):
        response = await self._with(None, lambda: panel.detalle("x", _request(), "sid"))
        self.assertEqual(response.status_code, 404)


MSGS = [
    {"id": 1, "sender": "user", "content": "cuanto cuesta la patente", "wa_message_id": "wamid.1", "created_at": _iso(timedelta(minutes=-30))},
    {"id": 2, "sender": "user", "content": "mensaje viejo sin id", "wa_message_id": None, "created_at": _iso(timedelta(minutes=-20))},
    {"id": 3, "sender": "expert", "content": "son 120 mil", "wa_message_id": None, "reply_to_id": 1, "created_at": _iso(timedelta(minutes=-10))},
]


class QuoteReplyTests(unittest.IsolatedAsyncioTestCase):
    def test_only_user_messages_with_whatsapp_id_can_be_quoted(self):
        self.assertEqual(panel._mensaje_citable(MSGS, "1")["id"], 1)
        self.assertIsNone(panel._mensaje_citable(MSGS, "2"))   # sin wamid
        self.assertIsNone(panel._mensaje_citable(MSGS, "3"))   # del experto
        self.assertIsNone(panel._mensaje_citable(MSGS, "999"))
        self.assertIsNone(panel._mensaje_citable(MSGS, "abc"))

    def test_thread_shows_reply_link_only_for_quotable_messages(self):
        html_hilo = panel._hilo_html(MSGS, "r1", puede_responder=True)
        self.assertEqual(html_hilo.count("Responder</a>"), 1)
        self.assertIn("?responder=1#mensaje", html_hilo)

    def test_thread_hides_reply_links_when_window_is_closed(self):
        self.assertNotIn("Responder</a>", panel._hilo_html(MSGS, "r1", puede_responder=False))

    def test_expert_message_shows_the_quoted_text(self):
        self.assertIn("cuanto cuesta la patente", panel._hilo_html(MSGS, "r1", puede_responder=True).split("son 120 mil")[0].split("admin-quote")[-1])

    def _patches(self):
        return [
            patch.object(panel, "_auth", new=AsyncMock(return_value={"comuna": "Recoleta", "nombre": "InnovaRecoleta"})),
            patch.object(panel, "get_expert_request", return_value=_row()),
            patch.object(panel, "_set_flash", new=AsyncMock()),
            patch.object(panel, "list_expert_messages", return_value=MSGS),
        ]

    async def _send(self, responder_a):
        patches = self._patches()
        for p in patches:
            p.start()
        try:
            with patch.object(panel, "send_text", new=AsyncMock()) as send,                  patch.object(panel, "add_expert_message") as add:
                await panel.enviar_mensaje("r1", _request(), mensaje="son 120 mil", responder_a=responder_a, financial_admin_session="sid")
            return send, add
        finally:
            for p in patches:
                p.stop()

    async def test_reply_is_sent_quoting_the_selected_message(self):
        send, add = await self._send("1")
        self.assertEqual(send.await_args.kwargs.get("reply_to_message_id"), "wamid.1")
        add.assert_called_once_with("r1", "expert", "son 120 mil", reply_to_id=1)

    async def test_invalid_or_foreign_quote_is_ignored_but_message_still_sent(self):
        for valor in ("2", "3", "999", "no-numero"):
            send, add = await self._send(valor)
            self.assertNotIn("reply_to_message_id", send.await_args.kwargs)
            add.assert_called_once_with("r1", "expert", "son 120 mil", reply_to_id=None)

    async def test_if_whatsapp_rejects_the_quote_it_resends_without_it(self):
        calls = []

        async def fake_send(phone, text, reply_to_message_id=None):
            calls.append(reply_to_message_id)
            if reply_to_message_id:
                raise panel.WhatsAppAPIError("id inválido")

        with patch.object(panel, "send_text", new=fake_send):
            ok = await panel._notificar("+56911112222", "hola", reply_to="wamid.malo")
        self.assertTrue(ok)
        self.assertEqual(calls, ["wamid.malo", None])

    async def test_detail_page_preselects_the_message_to_reply(self):
        request = _request()
        request.query_params = {"responder": "1"}
        patches = self._patches() + [patch.object(panel, "_pop_flash", new=AsyncMock(return_value=None))]
        for p in patches:
            p.start()
        try:
            response = await panel.detalle("abcd1234-0000", request, "sid")
        finally:
            for p in patches:
                p.stop()
        body = response.body.decode()
        self.assertIn("Respondiendo a: «cuanto cuesta la patente»", body)
        self.assertIn('name="responder_a" value="1"', body)


class AsesorAndReopenTests(unittest.IsolatedAsyncioTestCase):
    def test_asesor_phrases_start_the_request_flow(self):
        for frase in ("quiero hablar con un asesor", "Necesito un asesor", "contactar a un asesor por favor"):
            _, m = _flow(frase)
            m["create_expert_request"].assert_called_once()

    def test_negated_phrases_do_not_start_a_request(self):
        for frase in ("no necesito un asesor", "ya no quiero hablar con un experto"):
            result, m = _flow(frase)
            self.assertIsNone(result)
            m["create_expert_request"].assert_not_called()

    def test_first_message_after_the_expert_gets_an_acknowledgement(self):
        for ultimo in (None, {"sender": "expert", "created_at": _iso(timedelta(minutes=-1))}):
            with patch.object(expert_flow, "get_last_expert_message", return_value=ultimo),                  patch.object(expert_flow, "get_open_expert_request", return_value={"id": "r1", "status": "in_progress"}),                  patch.object(expert_flow, "add_expert_message") as add,                  patch.object(expert_flow, "touch_user_message"):
                result = expert_flow.handle_expert_message(USER, "hola", RESET)
            self.assertIn("Le envié tu mensaje al experto", result)
            add.assert_called_once()  # además se reenvía al panel

    def test_following_messages_in_a_row_are_not_acknowledged_again(self):
        result, _ = _flow("y otra cosa", open_request={"id": "r1", "status": "in_progress"})
        self.assertEqual(result, expert_flow.EXPERT_RELAY)

    def test_acknowledgement_returns_after_a_long_silence(self):
        antiguo = {"sender": "user", "created_at": _iso(timedelta(hours=-3))}
        with patch.object(expert_flow, "get_last_expert_message", return_value=antiguo),              patch.object(expert_flow, "get_open_expert_request", return_value={"id": "r1", "status": "in_progress"}),              patch.object(expert_flow, "add_expert_message"), patch.object(expert_flow, "touch_user_message"):
            result = expert_flow.handle_expert_message(USER, "sigo aquí", RESET)
        self.assertIn("Le envié tu mensaje", result)

    def test_database_error_when_checking_ack_does_not_break_the_relay(self):
        with patch.object(expert_flow, "get_last_expert_message", side_effect=RuntimeError("db")),              patch.object(expert_flow, "get_open_expert_request", return_value={"id": "r1", "status": "in_progress"}),              patch.object(expert_flow, "add_expert_message") as add, patch.object(expert_flow, "touch_user_message"):
            result = expert_flow.handle_expert_message(USER, "hola", RESET)
        self.assertEqual(result, expert_flow.EXPERT_RELAY)
        add.assert_called_once()

    def test_negation_cancels_a_pending_request(self):
        result, m = _flow("ya no necesito un asesor", open_request={"id": "r1", "status": "pending"})
        m["resolve_expert_request"].assert_called_once_with("r1", "emprendedor (canceló)")
        self.assertIn("cancelé tu solicitud", result)

    def test_negation_discards_a_draft(self):
        result, m = _flow("no quiero hablar con un experto", open_request=_draft(topic="sii"))
        m["delete_expert_draft"].assert_called_once_with("r1")
        self.assertIn("cancelé", result)

    def test_negation_during_conversation_relays_and_reminds_but_does_not_close(self):
        result, m = _flow("ya no necesito el asesor", open_request={"id": "r1", "status": "in_progress"})
        m["add_expert_message"].assert_called_once()
        m["resolve_expert_request"].assert_not_called()
        self.assertIn("terminar", result)

    def test_unrelated_negation_is_not_a_cancel(self):
        result, m = _flow("no, eso no lo necesito", open_request={"id": "r1", "status": "in_progress"})
        self.assertEqual(result, expert_flow.EXPERT_RELAY)
        m["resolve_expert_request"].assert_not_called()

    def test_user_ending_the_conversation_renews_the_window(self):
        _, m = _flow("terminar", open_request={"id": "r1", "status": "in_progress"})
        m["touch_user_message"].assert_called_once_with("r1")

    async def _reabrir(self, row, reopen_result=True, reopen_error=None):
        patches = [
            patch.object(panel, "_auth", new=AsyncMock(return_value={"comuna": "Recoleta", "nombre": "InnovaRecoleta"})),
            patch.object(panel, "get_expert_request", return_value=row),
            patch.object(panel, "_set_flash", new=AsyncMock()),
        ]
        for p_ in patches:
            p_.start()
        try:
            with patch.object(panel, "reopen_expert_request", return_value=reopen_result, side_effect=reopen_error) as reopen:
                response = await panel.reabrir("r1", _request(), "sid")
            return response, reopen, patches[2].new if False else None
        finally:
            for p_ in patches:
                p_.stop()

    async def test_admin_can_leave_a_closed_request_pending(self):
        response, reopen, _ = await self._reabrir(_row("resolved"))
        reopen.assert_called_once_with("r1", "Recoleta")
        self.assertEqual(response.status_code, 303)

    async def test_reopen_conflict_is_reported_not_raised(self):
        response, _, _ = await self._reabrir(_row("resolved"), reopen_error=RuntimeError("duplicate key"))
        self.assertEqual(response.status_code, 303)

    async def test_reopen_from_another_comuna_is_404(self):
        response, reopen, _ = await self._reabrir(None)
        self.assertEqual(response.status_code, 404)
        reopen.assert_not_called()

    async def test_resolved_page_offers_to_reopen_and_notes_who_closed_it(self):
        row = _row("resolved", closed_by="emprendedor", resolved_at=_iso(timedelta(minutes=-5)))
        patches = [
            patch.object(panel, "_auth", new=AsyncMock(return_value={"comuna": "Recoleta", "nombre": "x"})),
            patch.object(panel, "get_expert_request", return_value=row),
            patch.object(panel, "_pop_flash", new=AsyncMock(return_value=None)),
            patch.object(panel, "list_expert_messages", return_value=[]),
        ]
        for p_ in patches:
            p_.start()
        try:
            response = await panel.detalle("abcd1234-0000", _request(), "sid")
        finally:
            for p_ in patches:
                p_.stop()
        body = response.body.decode()
        self.assertIn("Dejar pendiente", body)
        self.assertIn("El emprendedor terminó la conversación", body)


class VoiceTests(unittest.TestCase):
    def test_topic_is_recognised_when_spoken(self):
        casos = {
            "Patentes.": "patentes", "es sobre fondos": "fondos", "Formalización": "formalizacion",
            "SII": "sii", "quiero saber de impuestos": "sii", "Permisos.": "permisos", "Otro": "otro",
        }
        for texto, esperado in casos.items():
            self.assertEqual(expert_flow.topic_from_text(texto), esperado, texto)

    def test_ambiguous_or_unknown_topic_is_not_guessed(self):
        self.assertIsNone(expert_flow.topic_from_text("patentes y fondos"))
        self.assertIsNone(expert_flow.topic_from_text("no sé"))

    def test_spoken_topic_advances_the_draft(self):
        result, m = _flow("Patentes.", open_request=_draft())
        m["set_expert_topic"].assert_called_once_with("r1", "patentes")
        self.assertEqual(result["type"], "buttons")

    def test_spoken_skip_and_cancel_with_trailing_period(self):
        _, m = _flow("Omitir.", open_request=_draft(topic="sii"))
        m["finalize_expert_request"].assert_called_once_with("r1", None)
        _, m = _flow("Cancelar.", open_request=_draft(topic="sii"))
        m["delete_expert_draft"].assert_called_once_with("r1")

    def test_spoken_reset_during_conversation_closes_request(self):
        result, m = _flow("Reiniciar.", open_request={"id": "r1", "status": "in_progress"})
        m["resolve_expert_request"].assert_called_once()
        self.assertIsNone(result)


class WebhookExpertCheckTests(unittest.TestCase):
    def test_open_request_exempts_audio_from_the_ambiguity_filter(self):
        with patch.object(webhook, "get_user", return_value={"id": "u1"}),              patch.object(webhook, "get_open_expert_request", return_value={"id": "r1", "status": "in_progress"}):
            self.assertTrue(webhook._has_open_expert_request("+56911112222"))

    def test_no_open_request_keeps_the_filter(self):
        with patch.object(webhook, "get_user", return_value={"id": "u1"}),              patch.object(webhook, "get_open_expert_request", return_value=None):
            self.assertFalse(webhook._has_open_expert_request("+56911112222"))

    def test_database_error_keeps_the_filter(self):
        with patch.object(webhook, "get_user", side_effect=RuntimeError("db")):
            self.assertFalse(webhook._has_open_expert_request("+56911112222"))


if __name__ == "__main__":
    unittest.main()
