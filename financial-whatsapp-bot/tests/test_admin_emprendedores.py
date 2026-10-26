import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import admin

ACCOUNT = {"comuna": "Recoleta", "nombre": "InnovaRecoleta"}
ROADMAP = [{"id": 1, "title": "Iniciar actividades en el SII", "done": False}]


def _usuario(i: int, formalizado: bool = False, rubro: str = "textil") -> dict:
    return {
        "phone": f"+5691234{i:04d}",
        "rubro": rubro,
        "roadmap": ROADMAP,
        "roadmap_completed_at": "2026-10-01T00:00:00Z" if formalizado else None,
    }


class EmprendedoresTests(unittest.TestCase):
    def setUp(self):
        self.usuarios = [_usuario(i, formalizado=i % 3 == 0, rubro="textil" if i % 2 else "alimentos") for i in range(60)]
        app = FastAPI()
        app.state.redis = object()
        app.include_router(admin.router)
        self.client = TestClient(app, follow_redirects=False)

        for target, new in (
            ("get_admin_session_account", AsyncMock(return_value=ACCOUNT)),
            ("get_users_by_comuna", lambda comuna: self.usuarios),
            ("get_admin_insights", lambda comuna: {"no_useful": 0, "topics": {}, "conflictivos": {}}),
        ):
            patcher = patch.object(admin, target, new)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _filas(self, response) -> int:
        return response.text.count('<span class="admin-badge ')

    def test_sin_sesion_redirige_al_login(self):
        with patch.object(admin, "get_admin_session_account", AsyncMock(return_value=None)):
            response = self.client.get("/admin/emprendedores")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/admin/login")

    def test_primera_pagina(self):
        response = self.client.get("/admin/emprendedores")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._filas(response), admin.EMPRENDEDORES_POR_PAGINA)
        self.assertIn("Mostrando 1–25 de 60", response.text)
        self.assertIn("Página 1 de 3", response.text)
        self.assertIn('href="/admin/emprendedores?pagina=2"', response.text)
        self.assertNotIn("Anterior", response.text)

    def test_ultima_pagina_y_pagina_fuera_de_rango(self):
        for pagina in ("3", "99"):
            response = self.client.get(f"/admin/emprendedores?pagina={pagina}")
            self.assertEqual(self._filas(response), 10)
            self.assertIn("Mostrando 51–60 de 60", response.text)
            self.assertNotIn("Siguiente", response.text)

    def test_pagina_invalida_vuelve_a_la_primera(self):
        response = self.client.get("/admin/emprendedores?pagina=abc")
        self.assertIn("Página 1 de 3", response.text)

    def test_busqueda_por_telefono_ignora_simbolos(self):
        response = self.client.get("/admin/emprendedores?q=9+1234+0007")
        self.assertEqual(self._filas(response), 1)
        self.assertIn("+56912340007", response.text)
        self.assertIn("(de 60 en total)", response.text)

    def test_filtros_de_estado_y_rubro_se_combinan_y_se_conservan_al_paginar(self):
        esperados = [u for u in self.usuarios if not u["roadmap_completed_at"] and u["rubro"] == "textil"]
        response = self.client.get("/admin/emprendedores?estado=progreso&rubro=textil")
        self.assertEqual(self._filas(response), len(esperados))
        self.assertNotIn("Formalizado</span>", response.text)
        self.assertIn('<option value="progreso" selected>', response.text)

        self.usuarios.extend(_usuario(100 + i) for i in range(30))
        response = self.client.get("/admin/emprendedores?estado=progreso&rubro=textil")
        self.assertIn('href="/admin/emprendedores?estado=progreso&amp;rubro=textil&amp;pagina=2"', response.text)

    def test_filtros_desconocidos_se_ignoran(self):
        response = self.client.get("/admin/emprendedores?estado=otro&rubro=mineria")
        self.assertIn("Mostrando 1–25 de 60", response.text)

    def test_sin_resultados(self):
        response = self.client.get("/admin/emprendedores?q=000000000")
        self.assertIn("Ningún emprendedor coincide con esos filtros.", response.text)
        self.assertNotIn("admin-pagination", response.text)

    def test_comuna_sin_emprendedores(self):
        self.usuarios.clear()
        response = self.client.get("/admin/emprendedores")
        self.assertIn("No hay emprendedores registrados todavía.", response.text)

    def test_el_panel_principal_enlaza_al_listado_en_vez_de_mostrar_la_tabla(self):
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/admin/emprendedores">Ver listado completo', response.text)
        self.assertNotIn("+56912340000", response.text)
        self.assertIn('<a href="/admin/emprendedores">Emprendedores</a>', response.text)


if __name__ == "__main__":
    unittest.main()
