import html
import json
import re
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import dependencies
from db.users import get_users_by_comuna
from routers import admin

ACCOUNT = {"comuna": "Recoleta", "nombre": "InnovaRecoleta"}
ROADMAP = [{"id": 1, "title": "Iniciar actividades en el SII", "done": False}]


def _ruta(total: int, pendiente: int) -> list[dict]:
    return [{"id": n, "title": f"Trámite {n}", "done": n < pendiente} for n in range(1, total + 1)]


def _usuario(i: int, formalizado: bool = False, rubro: str = "textil", roadmap: list[dict] | None = None) -> dict:
    return {
        "phone": f"+5691234{i:04d}",
        "rubro": rubro,
        "roadmap": ROADMAP if roadmap is None else roadmap,
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

    def test_muestra_el_nombre_escapado_y_corrige_mayusculas(self):
        self.usuarios[:] = [
            {**_usuario(1), "nombre_completo": "  maría gonzález "},
            {**_usuario(2), "nombre_completo": "Ana <b>de la Fuente</b>"},
            {**_usuario(3), "nombre_completo": None},
        ]
        response = self.client.get("/admin/emprendedores")
        self.assertIn("<th>Nombre</th>", response.text)
        self.assertIn("<td>María González</td>", response.text)
        self.assertIn("<td>Ana &lt;b&gt;de la Fuente&lt;/b&gt;</td>", response.text)
        self.assertIn("<td>—</td>", response.text)

    def test_la_consulta_de_la_comuna_trae_el_nombre(self):
        cliente = MagicMock()
        tabla = cliente.table.return_value
        tabla.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        with patch.object(dependencies, "supabase_admin", cliente):
            get_users_by_comuna("Recoleta")
        self.assertIn("nombre_completo", tabla.select.call_args.args[0])

    def _rutas_de_distinto_largo(self):
        self.usuarios[:] = [
            _usuario(1, rubro="textil", roadmap=_ruta(6, pendiente=3)),
            _usuario(2, rubro="textil", roadmap=_ruta(6, pendiente=6)),
            _usuario(3, rubro="alimentos", roadmap=_ruta(7, pendiente=3)),
            _usuario(4, rubro="alimentos", roadmap=_ruta(7, pendiente=7)),
            _usuario(5, rubro="alimentos", formalizado=True, roadmap=[]),
        ]

    def _opciones_de_hito(self, response) -> list[str]:
        select = re.search(r'<select name="hito"[^>]*>(.*?)</select>', response.text).group(1)
        return re.findall(r'<option value="(\d+)"', select)

    def test_las_opciones_de_hito_dependen_del_rubro(self):
        self._rutas_de_distinto_largo()
        for query, total in (("", 7), ("?rubro=textil", 6), ("?rubro=alimentos", 7)):
            response = self.client.get(f"/admin/emprendedores{query}")
            self.assertEqual(self._opciones_de_hito(response), [str(n) for n in range(1, total + 1)])

    def test_las_opciones_llevan_el_tramite_solo_con_un_rubro_elegido(self):
        self._rutas_de_distinto_largo()
        self.usuarios[0]["roadmap"][3]["title"] = "Patente <Municipal>"

        response = self.client.get("/admin/emprendedores?rubro=textil")
        self.assertIn('<option value="4">4 - Patente &lt;Municipal&gt; (0)</option>', response.text)

        response = self.client.get("/admin/emprendedores?rubro=alimentos")
        self.assertIn('<option value="4">4 - Trámite 4 (0)</option>', response.text)

        response = self.client.get("/admin/emprendedores")
        self.assertIn('<option value="4">4 (0)</option>', response.text)

    def test_las_opciones_cuentan_a_las_personas_en_cada_hito(self):
        self._rutas_de_distinto_largo()
        # Un perfil antiguo puede conservar su ruta aunque ya se haya formalizado: no cuenta.
        self.usuarios.append(_usuario(6, rubro="alimentos", formalizado=True, roadmap=_ruta(7, pendiente=3)))

        response = self.client.get("/admin/emprendedores?rubro=alimentos")
        self.assertIn('<option value="3">3 - Trámite 3 (1)</option>', response.text)
        self.assertIn('<option value="5">5 - Trámite 5 (0)</option>', response.text)

        response = self.client.get("/admin/emprendedores")
        self.assertIn('<option value="3">3 (2)</option>', response.text)
        self.assertIn('<option value="6">6 (1)</option>', response.text)
        self.assertIn('<option value="5">5 (0)</option>', response.text)

    def test_el_html_le_entrega_al_js_las_opciones_de_cada_rubro(self):
        self.usuarios[:] = [
            _usuario(1, rubro="textil", roadmap=_ruta(1, pendiente=1)),
            _usuario(2, rubro="alimentos", roadmap=_ruta(2, pendiente=1)),
        ]
        response = self.client.get("/admin/emprendedores")
        atributo = re.search(r'data-opciones-por-rubro="([^"]*)"', response.text).group(1)
        self.assertEqual(
            json.loads(html.unescape(atributo)),
            {
                "textil": [["1", "1 - Trámite 1 (1)"]],
                "alimentos": [["1", "1 - Trámite 1 (1)"], ["2", "2 - Trámite 2 (0)"]],
                "": [["1", "1 (2)"], ["2", "2 (0)"]],
            },
        )

    def test_filtro_de_hito_usa_el_hito_pendiente(self):
        self._rutas_de_distinto_largo()
        response = self.client.get("/admin/emprendedores?hito=3")
        self.assertEqual(self._filas(response), 2)
        self.assertIn('<option value="3" selected>', response.text)
        self.assertIn("+56912340001", response.text)
        self.assertIn("+56912340003", response.text)

        response = self.client.get("/admin/emprendedores?hito=3&rubro=alimentos")
        self.assertEqual(self._filas(response), 1)
        self.assertIn("+56912340003", response.text)

    def test_hito_que_el_rubro_no_tiene_se_ignora(self):
        self._rutas_de_distinto_largo()
        response = self.client.get("/admin/emprendedores?hito=7&rubro=textil")
        self.assertEqual(self._filas(response), 2)
        self.assertNotIn(" selected>7<", response.text)

    def test_hito_invalido_se_ignora(self):
        self._rutas_de_distinto_largo()
        for hito in ("0", "8", "abc"):
            response = self.client.get(f"/admin/emprendedores?hito={hito}")
            self.assertEqual(self._filas(response), 5)

    def test_filtro_de_hito_excluye_a_los_formalizados(self):
        self._rutas_de_distinto_largo()
        # Un perfil antiguo puede conservar su ruta aunque ya se haya formalizado.
        self.usuarios.append(_usuario(6, rubro="alimentos", formalizado=True, roadmap=_ruta(7, pendiente=3)))
        response = self.client.get("/admin/emprendedores?hito=3")
        self.assertEqual(self._filas(response), 2)
        self.assertNotIn("+56912340006", response.text)

    def test_con_formalizados_el_filtro_de_hito_se_desactiva(self):
        self._rutas_de_distinto_largo()
        response = self.client.get("/admin/emprendedores?hito=3&estado=formalizado")
        self.assertEqual(self._filas(response), 1)
        self.assertIn("+56912340005", response.text)
        self.assertNotIn("Ningún emprendedor coincide", response.text)
        self.assertRegex(response.text, r'<select name="hito"[^>]* disabled>')
        self.assertNotIn('<option value="3" selected>', response.text)

        response = self.client.get("/admin/emprendedores?estado=progreso")
        self.assertNotRegex(response.text, r'<select name="hito"[^>]* disabled>')

    def test_filtro_de_hito_se_conserva_al_paginar(self):
        self.usuarios[:] = [_usuario(i, roadmap=_ruta(6, pendiente=2)) for i in range(30)]
        response = self.client.get("/admin/emprendedores?hito=2")
        self.assertIn('href="/admin/emprendedores?hito=2&amp;pagina=2"', response.text)

    def test_comuna_sin_rutas_no_ofrece_hitos(self):
        self.usuarios[:] = [_usuario(1, formalizado=True, roadmap=[])]
        response = self.client.get("/admin/emprendedores")
        self.assertEqual(self._opciones_de_hito(response), [])

    def test_el_panel_principal_enlaza_al_listado_en_vez_de_mostrar_la_tabla(self):
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/admin/emprendedores">Ver listado completo', response.text)
        self.assertNotIn("+56912340000", response.text)
        self.assertIn('<a href="/admin/emprendedores">Emprendedores</a>', response.text)


if __name__ == "__main__":
    unittest.main()
