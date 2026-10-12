import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import expert_flow
from routers import admin_solicitudes as panel

RESET = {"reiniciar", "menu_reiniciar"}
USER = {"id": "user-1", "comuna": "Recoleta"}


def _flow(message, open_request=None, user=USER):
    with patch.object(expert_flow, "get_open_expert_request", return_value=open_request), \
         patch.object(expert_flow, "create_expert_request", return_value={"id": "r1"}) as create, \
         patch.object(expert_flow, "add_expert_message") as add, \
         patch.object(expert_flow, "touch_user_message") as touch, \
         patch.object(expert_flow, "resolve_expert_request") as resolve:
        result = expert_flow.handle_expert_message(user, message, RESET)
    return result, create, add, touch, resolve


class ExpertFlowTests(unittest.TestCase):
    def test_entry_creates_request_and_promises_deadline(self):
        result, create, *_ = _flow("menu_experto")
        create.assert_called_once_with("user-1", "Recoleta")
        self.assertIn("dentro de 24 horas", result)

    def test_natural_phrase_also_creates_request(self):
        _, create, *_ = _flow("quiero hablar con un experto por favor")
        create.assert_called_once()

    def test_comuna_without_admin_is_told_it_is_not_available(self):
        result, create, *_ = _flow("menu_experto", user={"id": "u", "comuna": "Providencia"})
        create.assert_not_called()
        self.assertIn("solo para Recoleta y El Bosque", result)

    def test_second_request_is_not_duplicated(self):
        result, create, *_ = _flow("menu_experto", open_request={"id": "r1", "status": "pending"})
        create.assert_not_called()
        self.assertIn("solicitud en curso", result)

    def test_pending_request_leaves_normal_assistant_running(self):
        result, *_ = _flow("¿qué es el IVA?", open_request={"id": "r1", "status": "pending"})
        self.assertIsNone(result)

    def test_unrelated_message_without_request_is_ignored(self):
        result, create, *_ = _flow("hola")
        self.assertIsNone(result)
        create.assert_not_called()

    def test_in_progress_relays_message_and_pauses_ai(self):
        result, _, add, touch, _ = _flow("gracias", open_request={"id": "r1", "status": "in_progress"})
        self.assertEqual(result, expert_flow.EXPERT_RELAY)
        add.assert_called_once_with("r1", "user", "gracias")
        touch.assert_called_once_with("r1")

    def test_user_can_end_conversation(self):
        result, _, add, _, resolve = _flow("Terminar", open_request={"id": "r1", "status": "in_progress"})
        resolve.assert_called_once_with("r1", "emprendedor")
        add.assert_not_called()
        self.assertIn("Terminó la conversación", result)

    def test_reset_closes_request_and_continues_to_reset(self):
        result, _, _, _, resolve = _flow("reiniciar", open_request={"id": "r1", "status": "in_progress"})
        resolve.assert_called_once()
        self.assertIsNone(result)

    def test_database_error_falls_back_to_normal_flow(self):
        with patch.object(expert_flow, "get_open_expert_request", side_effect=RuntimeError("db")):
            self.assertIsNone(expert_flow.handle_expert_message(USER, "menu_experto", RESET))

    def test_user_without_id_is_ignored(self):
        result, *_ = _flow("menu_experto", user={"comuna": "Recoleta"})
        self.assertIsNone(result)


def _iso(delta):
    return (datetime.now(timezone.utc) + delta).isoformat()


def _row(status="in_progress", last=timedelta(hours=-1)):
    return {
        "id": "abcd1234-0000", "status": status, "user_last_message_at": _iso(last),
        "created_at": _iso(timedelta(hours=-2)),
        "users": {"phone": "+56911112222", "rubro": "alimentos", "roadmap": []},
    }


def _request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=object())))


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


class AdminPanelTests(unittest.IsolatedAsyncioTestCase):
    def _patch(self, row, **extra):
        stack = [
            patch.object(panel, "_auth", new=AsyncMock(return_value={"comuna": "Recoleta", "nombre": "InnovaRecoleta"})),
            patch.object(panel, "get_expert_request", return_value=row),
            patch.object(panel, "_set_flash", new=AsyncMock()),
            patch.object(panel, "_pop_flash", new=AsyncMock(return_value=None)),
            patch.object(panel, "list_expert_messages", return_value=[]),
        ]
        return stack

    async def test_detail_page_never_shows_the_phone_number(self):
        patches = self._patch(_row())
        for p in patches:
            p.start()
        try:
            response = await panel.detalle("abcd1234-0000", _request(), "sid")
        finally:
            for p in patches:
                p.stop()
        self.assertNotIn(b"56911112222", response.body)

    async def test_take_notifies_user_and_marks_in_progress(self):
        patches = self._patch(_row("pending"))
        for p in patches:
            p.start()
        try:
            with patch.object(panel, "take_expert_request", return_value=True) as take, \
                 patch.object(panel, "send_text", new=AsyncMock()) as send:
                response = await panel.tomar("r1", _request(), "sid")
        finally:
            for p in patches:
                p.stop()
        take.assert_called_once()
        self.assertEqual(send.await_args.args[0], "+56911112222")
        self.assertEqual(response.status_code, 303)

    async def test_take_is_refused_when_window_closed(self):
        patches = self._patch(_row("pending", last=timedelta(hours=-30)))
        for p in patches:
            p.start()
        try:
            with patch.object(panel, "take_expert_request") as take:
                await panel.tomar("r1", _request(), "sid")
        finally:
            for p in patches:
                p.stop()
        take.assert_not_called()

    async def test_message_is_sent_and_saved_inside_window(self):
        patches = self._patch(_row())
        for p in patches:
            p.start()
        try:
            with patch.object(panel, "send_text", new=AsyncMock()) as send, \
                 patch.object(panel, "add_expert_message") as add:
                await panel.enviar_mensaje("r1", _request(), mensaje="Hola, ¿en qué te ayudo?", financial_admin_session="sid")
        finally:
            for p in patches:
                p.stop()
        self.assertIn("InnovaRecoleta", send.await_args.args[1])
        add.assert_called_once_with("r1", "expert", "Hola, ¿en qué te ayudo?")

    async def test_message_is_blocked_outside_window(self):
        patches = self._patch(_row(last=timedelta(hours=-30)))
        for p in patches:
            p.start()
        try:
            with patch.object(panel, "send_text", new=AsyncMock()) as send, \
                 patch.object(panel, "add_expert_message") as add:
                await panel.enviar_mensaje("r1", _request(), mensaje="Hola", financial_admin_session="sid")
        finally:
            for p in patches:
                p.stop()
        send.assert_not_awaited()
        add.assert_not_called()

    async def test_request_from_another_comuna_is_404(self):
        patches = self._patch(None)
        for p in patches:
            p.start()
        try:
            response = await panel.detalle("x", _request(), "sid")
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
