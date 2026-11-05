"""Pruebas locales del simulador y de las cookies, sin servicios externos."""
import os
import runpy
import sys
import unittest
from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from routers import admin, portal, test as simulator


class WebSecurityTests(unittest.TestCase):
    def test_configuration_fails_closed_except_for_explicit_local_mode(self):
        for env, chat, secure in (
            ({}, False, True),
            ({"DEBUG": "true", "ENABLE_TEST_CHAT": "true"}, False, True),
            ({"APP_ENV": "production", "ENABLE_TEST_CHAT": "true"}, False, True),
            ({"APP_ENV": "local"}, False, False),
            ({"APP_ENV": "local", "ENABLE_TEST_CHAT": "true"}, True, False),
            ({"APP_ENV": "local", "ENABLE_TEST_CHAT": "true", "RAILWAY_PROJECT_ID": "test"}, False, True),
        ):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True), patch("dotenv.load_dotenv"):
                settings = runpy.run_path(config.__file__)
                self.assertEqual(settings["ENABLE_TEST_CHAT"], chat)
                self.assertEqual(settings["SESSION_COOKIE_SECURE"], secure)

    def test_simulator_routes_are_absent_when_disabled_and_usable_when_enabled(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), patch.object(config, "ENABLE_TEST_CHAT", enabled), patch.object(sys, "path", sys.path.copy()):
                app = runpy.run_path(str(Path(config.__file__).with_name("main.py")))["app"]
                # No se arranca el lifespan: el test no necesita credenciales ni Redis.
                client = TestClient(app)
                with patch.object(simulator, "route_message", return_value="prueba") as route, patch.object(simulator, "is_rate_limit_exempt", return_value=True):
                    self.assertEqual(client.get("/test/chat").status_code, 200 if enabled else 404)
                    response = client.post("/test/chat", json={"phone": "test", "message": "hola"})
                    self.assertEqual(response.status_code, 200 if enabled else 404)
                    if enabled:
                        route.assert_called_once_with("test", "hola")
                    else:
                        route.assert_not_called()

    def test_both_panels_set_and_expire_session_cookies_with_expected_flags(self):
        app = FastAPI()
        app.state.redis = object()
        app.include_router(admin.router)
        app.include_router(portal.router)
        for secure in (True, False):
            client = TestClient(app, base_url=("https" if secure else "http") + "://localhost", follow_redirects=False)
            with (
                self.subTest(secure=secure),
                patch.object(admin, "SESSION_COOKIE_SECURE", secure),
                patch.object(portal, "SESSION_COOKIE_SECURE", secure),
                patch.object(admin, "authenticate_admin", return_value={"comuna": "Recoleta"}),
                patch.object(admin, "create_admin_session", new=AsyncMock(return_value="admin-id")),
                patch.object(admin, "destroy_admin_session", new_callable=AsyncMock) as admin_logout,
                patch.object(portal, "redeem_access_token", new=AsyncMock(return_value="test")),
                patch.object(portal, "create_session", new=AsyncMock(return_value="portal-id")),
                patch.object(portal, "destroy_session", new_callable=AsyncMock) as portal_logout,
            ):
                for response, name, age in (
                    (client.post("/admin/login", data={"username": "demo", "password": "demo"}), "financial_admin_session", 86400),
                    (client.get("/portal/acceso?token=test"), "financial_session", 604800),
                    (client.get("/admin/logout"), "financial_admin_session", 0),
                    (client.get("/portal/logout"), "financial_session", 0),
                ):
                    self.assertEqual(response.status_code, 303)
                    cookie = SimpleCookie(response.headers["set-cookie"])[name]
                    self.assertEqual(bool(cookie["secure"]), secure)
                    self.assertTrue(cookie["httponly"])
                    self.assertEqual(cookie["samesite"].lower(), "lax")
                    self.assertEqual(cookie["max-age"], str(age))
                admin_logout.assert_awaited_once_with(app.state.redis, "admin-id")
                portal_logout.assert_awaited_once_with(app.state.redis, "portal-id")
