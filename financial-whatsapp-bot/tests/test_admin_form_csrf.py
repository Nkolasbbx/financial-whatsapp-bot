import re
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import admin, admin_solicitudes as solicitudes
from services import admin_auth
from tests.test_admin_documentos import FakeRedis, ACCOUNT
from tests.test_expert_requests import _row


class AdminFormCsrfTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.state.redis = self.redis = FakeRedis()
        app.include_router(admin.router)
        app.include_router(solicitudes.router)
        self.redis.data.update({"admin_session:A": "muni", "admin_session:B": "muni"})
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(admin_auth.ADMIN_ACCOUNTS, {"muni": ACCOUNT}))
        self.client = TestClient(app, follow_redirects=False)
        self.client.cookies.set("financial_admin_session", "A")

    def test_neither_missing_wrong_nor_foreign_token_can_trigger_any_action(self):
        self.redis.data.update({"admin_csrf:A": "alpha", "admin_csrf:B": "beta"})
        targets = [
            (admin, "enqueue_document_ingestion"), (admin, "get_ingestion_job"),
            (admin, "delete_ingested_document"), (solicitudes, "get_expert_request"),
            (solicitudes, "_notificar"), (solicitudes, "add_expert_message"),
            (solicitudes, "take_expert_request"), (solicitudes, "resolve_expert_request"),
            (solicitudes, "reopen_expert_request"),
        ]
        effects = [self.stack.enter_context(patch.object(module, name)) for module, name in targets]
        paths = ["/admin/documentos/subir", "/admin/documentos/job/eliminar"] + [
            "/admin/solicitudes/r1/" + action for action in ("tomar", "mensaje", "resolver", "reabrir")
        ]
        for path in paths:
            for token in (None, "incorrecto", "beta", "inválido"):
                data = {"mensaje": "hola"}
                if token is not None:
                    data["csrf_token"] = token
                with self.subTest(path=path, token=token):
                    files = {"archivos": ("hola.md", b"hola")} if path.endswith("/subir") else None
                    response = self.client.post(path, data=data, files=files)
                    self.assertEqual(response.status_code, 403)
        for effect in effects:
            effect.assert_not_called()

    def test_all_mutating_forms_render_the_token_for_the_current_session(self):
        job = {"id": "job", "file_name": "demo.md", "status": "done", "comuna": "Recoleta"}
        self.stack.enter_context(patch.object(admin, "list_recent_jobs", return_value=[job]))
        self.stack.enter_context(patch.object(admin, "list_active_documents", return_value=[]))
        self.stack.enter_context(patch.object(solicitudes, "list_expert_messages", return_value=[]))
        pages = [(self.client.get("/admin/documentos"), ["subir", "eliminar"])]
        for state, actions in (("pending", ["tomar"]), ("in_progress", ["mensaje", "resolver"]), ("resolved", ["reabrir"])):
            with patch.object(solicitudes, "get_expert_request", return_value=_row(state)):
                pages.append((self.client.get("/admin/solicitudes/r1"), actions))
        token = self.redis.data["admin_csrf:A"]
        for response, actions in pages:
            self.assertEqual(response.status_code, 200)
            for action in actions:
                self.assertRegex(response.text, rf'(?s)<form\b[^>]*action="[^"]*/{action}"[^>]*>(?:(?!</form>).)*name="csrf_token" value="{re.escape(token)}"')

    def test_valid_token_preserves_comuna_check_and_allows_legitimate_resolution(self):
        self.redis.data["admin_csrf:A"] = "alpha"
        with patch.object(solicitudes, "get_expert_request", return_value=None) as lookup, \
             patch.object(solicitudes, "resolve_expert_request", return_value=True) as resolve, \
             patch.object(solicitudes, "_notificar", new_callable=AsyncMock) as notify:
            def submit():
                return self.client.post("/admin/solicitudes/r1/resolver", data={"csrf_token": "alpha"})
            self.assertEqual(submit().status_code, 404)
            lookup.assert_called_once_with("r1", "Recoleta")
            resolve.assert_not_called()
            notify.assert_not_awaited()
            lookup.return_value = _row()
            self.assertEqual(submit().status_code, 303)
            resolve.assert_called_once_with("r1", ACCOUNT["nombre"], ACCOUNT["comuna"])
            notify.assert_awaited_once()
