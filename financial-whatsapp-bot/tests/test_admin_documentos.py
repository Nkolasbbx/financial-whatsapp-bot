import json
import unittest
import unittest.mock
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import admin
from services import ingestion_jobs
from services.ingestion_jobs import validate_batch

ACCOUNT = {"comuna": "Recoleta", "nombre": "InnovaRecoleta"}


class FakeRedis:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def delete(self, key):
        self.data.pop(key, None)


class BrowserFormClient(TestClient):
    """Envía el mismo campo oculto que el formulario HTML de la sesión."""
    def post(self, url, **kwargs):
        sid = self.cookies.get("financial_admin_session")
        token = self.app.state.redis.data.setdefault("admin_csrf:" + sid, "token-" + sid)
        data = {"csrf_token": token, **kwargs.pop("data", {})}
        return super().post(url, data=data, **kwargs)


class ValidateBatchTests(unittest.TestCase):
    def test_lote_valido(self):
        self.assertIsNone(validate_batch([("a.pdf", 1000), ("b.md", 2000)]))

    def test_lote_vacio(self):
        self.assertIsNotNone(validate_batch([]))

    def test_excede_cantidad_de_archivos(self):
        files = [(f"{i}.md", 10) for i in range(ingestion_jobs.INGESTION_MAX_BATCH_FILES + 1)]
        self.assertIn("Máximo", validate_batch(files))

    def test_excede_tamano_total(self):
        mitad = ingestion_jobs.MAX_BATCH_BYTES // 2 + 1
        self.assertIn("total", validate_batch([("a.pdf", mitad), ("b.pdf", mitad)]))

    def test_nombres_repetidos_sin_importar_mayusculas(self):
        self.assertIn("mismo nombre", validate_batch([("Doc.pdf", 10), ("doc.PDF", 10)]))


class SubirDocumentosTests(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        app = FastAPI()
        app.state.redis = self.redis
        app.include_router(admin.router)
        self.client = BrowserFormClient(app, follow_redirects=False)
        self.client.cookies.set("financial_admin_session", "sesion-1")

        for target, new in (
            ("get_admin_session_account", AsyncMock(return_value=ACCOUNT)),
            ("list_recent_jobs", lambda *a, **k: []),
            ("list_active_documents", lambda *a, **k: []),
        ):
            patcher = patch.object(admin, target, new)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.enqueue = AsyncMock(return_value="job-id")
        patcher = patch.object(admin, "enqueue_document_ingestion", self.enqueue)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _flash(self):
        return json.loads(self.redis.data["admin_flash:sesion-1"])

    def test_lote_valido_redirige_y_encola_con_metadata_por_archivo(self):
        response = self.client.post(
            "/admin/documentos/subir",
            files=[
                ("archivos", ("a.md", b"# a\n\ntexto", "text/markdown")),
                ("archivos", ("b.md", b"# b\n\ntexto", "text/markdown")),
            ],
            data={
                "rubros_0": ["textil"],
                "vigencia_hasta_0": "2027-12-31",
                "rubros_1": ["alimentos", "general"],
            },
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/admin/documentos")
        self.assertEqual(self.enqueue.await_count, 2)

        primero, segundo = (c.kwargs for c in self.enqueue.await_args_list)
        self.assertEqual(primero["file_name"], "a.md")
        self.assertEqual(primero["rubros"], ["textil"])
        self.assertEqual(primero["vigencia_hasta"], "2027-12-31")
        self.assertEqual(primero["comuna"], "Recoleta")
        self.assertEqual(segundo["rubros"], ["alimentos", "general"])
        self.assertIsNone(segundo["vigencia_hasta"])
        self.assertEqual(self._flash()["kind"], "ok")

    def test_sin_rubros_usa_general(self):
        self.client.post(
            "/admin/documentos/subir",
            files=[("archivos", ("a.md", b"# a", "text/markdown"))],
        )
        self.assertEqual(self.enqueue.await_args.kwargs["rubros"], ["general"])

    def test_lote_mixto_encola_solo_los_validos(self):
        response = self.client.post(
            "/admin/documentos/subir",
            files=[
                ("archivos", ("a.md", b"# a", "text/markdown")),
                ("archivos", ("malo.txt", b"x", "text/plain")),
                ("archivos", ("c.md", b"# c", "text/markdown")),
            ],
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual([c.kwargs["file_name"] for c in self.enqueue.await_args_list], ["a.md", "c.md"])
        flash = self._flash()
        self.assertEqual(flash["kind"], "warn")
        self.assertTrue(any("malo.txt" in d for d in flash["details"]))

    def test_fecha_invalida_rechaza_solo_ese_archivo(self):
        self.client.post(
            "/admin/documentos/subir",
            files=[
                ("archivos", ("a.md", b"# a", "text/markdown")),
                ("archivos", ("b.md", b"# b", "text/markdown")),
            ],
            data={"vigencia_desde_0": "31/12/2026"},
        )
        self.assertEqual([c.kwargs["file_name"] for c in self.enqueue.await_args_list], ["b.md"])
        self.assertEqual(self._flash()["kind"], "warn")

    def test_lote_sobre_el_limite_no_encola_nada(self):
        files = [
            ("archivos", (f"{i}.md", b"# x", "text/markdown"))
            for i in range(ingestion_jobs.INGESTION_MAX_BATCH_FILES + 1)
        ]
        response = self.client.post("/admin/documentos/subir", files=files)

        self.assertEqual(response.status_code, 303)
        self.enqueue.assert_not_awaited()
        self.assertEqual(self._flash()["kind"], "error")

    def test_fallo_al_encolar_un_archivo_no_pierde_los_demas(self):
        self.enqueue.side_effect = [RuntimeError("storage caído"), "job-2"]
        self.client.post(
            "/admin/documentos/subir",
            files=[
                ("archivos", ("a.md", b"# a", "text/markdown")),
                ("archivos", ("b.md", b"# b", "text/markdown")),
            ],
        )
        self.assertEqual(self.enqueue.await_count, 2)
        flash = self._flash()
        self.assertEqual(flash["kind"], "warn")
        self.assertTrue(any("a.md" in d for d in flash["details"]))

    def test_get_muestra_el_flash_una_sola_vez(self):
        self.client.post(
            "/admin/documentos/subir",
            files=[("archivos", ("a.md", b"# a", "text/markdown"))],
        )

        primera = self.client.get("/admin/documentos")
        segunda = self.client.get("/admin/documentos")

        self.assertIn("admin-flash ok", primera.text)
        self.assertNotIn("admin-flash ok", segunda.text)

    def test_flash_escapa_html_en_nombres_de_archivo(self):
        self.client.post(
            "/admin/documentos/subir",
            files=[("archivos", ("<img src=x>.txt", b"x", "text/plain"))],
        )
        html_page = self.client.get("/admin/documentos").text
        self.assertNotIn("<img src=x>", html_page)
        self.assertIn("&lt;img src=x&gt;", html_page)

    def test_municipio_no_puede_elegir_ambito(self):
        self.client.post(
            "/admin/documentos/subir",
            files=[("archivos", ("a.md", b"# a", "text/markdown"))],
            data={"ambito_0": "general"},
        )
        self.assertEqual(self.enqueue.await_args.kwargs["comuna"], "Recoleta")

    def test_fuente_opcional_viaja_al_job(self):
        self.client.post(
            "/admin/documentos/subir",
            files=[
                ("archivos", ("a.pdf", b"%PDF", "application/pdf")),
                ("archivos", ("b.md", b"# b", "text/markdown")),
            ],
            data={"fuente_0": "Ordenanza de patentes", "fuente_url_0": "https://muni.cl/ordenanza.pdf"},
        )
        primero, segundo = (c.kwargs for c in self.enqueue.await_args_list)
        self.assertEqual(primero["source"], "Ordenanza de patentes")
        self.assertEqual(primero["source_url"], "https://muni.cl/ordenanza.pdf")
        self.assertIsNone(segundo["source"])
        self.assertIsNone(segundo["source_url"])

    def test_url_de_fuente_invalida_rechaza_solo_ese_archivo(self):
        self.client.post(
            "/admin/documentos/subir",
            files=[
                ("archivos", ("a.md", b"# a", "text/markdown")),
                ("archivos", ("b.md", b"# b", "text/markdown")),
            ],
            data={"fuente_url_0": "javascript:alert(1)"},
        )
        self.assertEqual([c.kwargs["file_name"] for c in self.enqueue.await_args_list], ["b.md"])
        self.assertTrue(any("URL" in d for d in self._flash()["details"]))

    def test_municipio_ve_documentos_nacionales_sin_eliminar(self):
        nacional = {
            "id": "job-n", "file_name": "inicio_sii.md", "comuna": "general",
            "rubros": ["general"], "vigencia_hasta": None, "status": "done",
        }
        with patch.object(admin, "list_active_documents", lambda *a, **k: [nacional, dict(nacional, id="job-viejo")]):
            page = self.client.get("/admin/documentos").text

        tabla = page.split("Documentos nacionales (FinancIAl)")[1]
        self.assertEqual(tabla.count("inicio_sii.md"), 1)  # re-subida: solo el más reciente
        self.assertNotIn("Eliminar", tabla)
        self.assertNotIn("ambitos", page)  # el selector de ámbito es solo del equipo

    def test_municipio_filtra_solo_dentro_de_su_comuna(self):
        recientes = unittest.mock.MagicMock(return_value=[])
        with patch.object(admin, "list_recent_jobs", recientes):
            page = self.client.get("/admin/documentos", params={"ambito": "El Bosque", "estado": "failed"}).text

        recientes.assert_called_once_with("Recoleta", limit=20, nombre="", estado="failed", rubro="")
        self.assertIn('aria-label="Filtrar documentos"', page)
        self.assertNotIn('name="ambito"', page)


EQUIPO = {"comuna": "general", "nombre": "Equipo FinancIAl", "rol": "equipo"}


class EquipoDocumentosTests(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        app = FastAPI()
        app.state.redis = self.redis
        app.include_router(admin.router)
        self.client = BrowserFormClient(app, follow_redirects=False)
        self.client.cookies.set("financial_admin_session", "sesion-equipo")

        self.session_mock = AsyncMock(return_value=EQUIPO)
        self.list_recent = unittest.mock.MagicMock(return_value=[])
        for target, new in (
            ("get_admin_session_account", self.session_mock),
            ("list_recent_jobs", self.list_recent),
            ("list_active_documents", lambda *a, **k: []),
        ):
            patcher = patch.object(admin, target, new)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.enqueue = AsyncMock(return_value="job-id")
        patcher = patch.object(admin, "enqueue_document_ingestion", self.enqueue)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_equipo_elige_ambito_por_archivo(self):
        self.client.post(
            "/admin/documentos/subir",
            files=[
                ("archivos", ("nacional.md", b"# a", "text/markdown")),
                ("archivos", ("bosque.md", b"# b", "text/markdown")),
            ],
            data={"ambito_0": "general", "ambito_1": "El Bosque"},
        )
        self.assertEqual([c.kwargs["comuna"] for c in self.enqueue.await_args_list], ["general", "El Bosque"])
        self.assertEqual(self.enqueue.await_args.kwargs["uploaded_by"], "Equipo FinancIAl")

    def test_ambito_fuera_de_la_lista_se_rechaza(self):
        self.client.post(
            "/admin/documentos/subir",
            files=[("archivos", ("a.md", b"# a", "text/markdown"))],
            data={"ambito_0": "Providencia"},
        )
        self.enqueue.assert_not_awaited()
        self.assertEqual(json.loads(self.redis.data["admin_flash:sesion-equipo"])["kind"], "error")

    def test_rutas_de_documentos_habilitan_al_equipo(self):
        self.client.get("/admin/documentos")
        self.assertTrue(self.session_mock.await_args.kwargs.get("allow_equipo"))

    def test_pagina_muestra_selector_e_historial_de_todas_las_comunas(self):
        page = self.client.get("/admin/documentos").text
        self.list_recent.assert_called_once_with(None, limit=20, nombre="", estado="", rubro="")
        self.assertIn("Nacional (todas las comunas)", page)
        self.assertIn("<th>Ámbito</th>", page)
        self.assertNotIn("Documentos nacionales (FinancIAl)", page)
        self.assertNotIn('href="/admin/emprendedores"', page)

    def test_equipo_filtra_por_ambito_y_demas_campos(self):
        page = self.client.get(
            "/admin/documentos",
            params={"q": " inicio_sii ", "ambito": "El Bosque", "rubro": "general", "estado": "done"},
        ).text
        self.list_recent.assert_called_once_with(
            "El Bosque", limit=20, nombre="inicio_sii", estado="done", rubro="general"
        )
        self.assertIn('<option value="El Bosque" selected>', page)
        self.assertIn('value="inicio_sii"', page)
        self.assertIn("Ningún documento coincide con esos filtros.", page)

    def test_valores_de_filtro_fuera_de_las_listas_se_ignoran(self):
        self.client.get(
            "/admin/documentos",
            params={"ambito": "Providencia", "rubro": "inventado", "estado": "eliminado"},
        )
        self.list_recent.assert_called_once_with(None, limit=20, nombre="", estado="", rubro="")

    def test_eliminar_conserva_los_filtros_de_la_busqueda(self):
        job = {"id": "job-1", "file_name": "patentes.md", "comuna": "El Bosque", "status": "done"}
        self.list_recent.return_value = [job]

        page = self.client.get("/admin/documentos", params={"q": "patentes", "estado": "done"}).text
        self.assertIn("/admin/documentos/job-1/eliminar?q=patentes&amp;estado=done", page)

        with patch.object(admin, "get_ingestion_job", return_value=job), \
             patch.object(admin, "delete_ingested_document"):
            response = self.client.post("/admin/documentos/job-1/eliminar?q=patentes&estado=done")
        self.assertEqual(response.headers["location"], "/admin/documentos?q=patentes&estado=done")

    def test_equipo_consulta_y_elimina_jobs_de_cualquier_comuna(self):
        job = {"id": "job-1", "file_name": "patentes.md", "comuna": "El Bosque", "status": "done"}
        with patch.object(admin, "get_ingestion_job", return_value=job), \
             patch.object(admin, "delete_ingested_document") as delete_mock:
            estado = self.client.get("/admin/documentos/estado/job-1")
            self.client.post("/admin/documentos/job-1/eliminar")

        self.assertEqual(estado.status_code, 200)
        delete_mock.assert_called_once_with("patentes.md", "El Bosque", "job-1", "Equipo FinancIAl")

    def test_dashboard_redirige_al_equipo_a_documentos(self):
        response = self.client.get("/admin")
        self.assertEqual(response.headers["location"], "/admin/documentos")


class MunicipioPermisosTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.state.redis = FakeRedis()
        app.include_router(admin.router)
        self.client = BrowserFormClient(app, follow_redirects=False)
        self.client.cookies.set("financial_admin_session", "sesion-1")
        patcher = patch.object(admin, "get_admin_session_account", AsyncMock(return_value=ACCOUNT))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_municipio_no_ve_jobs_nacionales(self):
        job = {"id": "job-n", "file_name": "inicio_sii.md", "comuna": "general", "status": "done"}
        with patch.object(admin, "get_ingestion_job", return_value=job), \
             patch.object(admin, "delete_ingested_document") as delete_mock:
            estado = self.client.get("/admin/documentos/estado/job-n")
            eliminar = self.client.post("/admin/documentos/job-n/eliminar")

        self.assertEqual(estado.status_code, 404)
        self.assertEqual(eliminar.status_code, 404)
        delete_mock.assert_not_called()


class AdminAuthEquipoTests(unittest.IsolatedAsyncioTestCase):
    async def test_cuenta_del_equipo_solo_con_allow_equipo(self):
        from services import admin_auth

        redis = FakeRedis()
        cuentas = {
            "financial": {"password": "x", "comuna": "general", "nombre": "Equipo FinancIAl", "rol": "equipo"},
            "recoleta": {"password": "y", "comuna": "Recoleta", "nombre": "InnovaRecoleta", "rol": "municipio"},
        }
        with patch.object(admin_auth, "ADMIN_ACCOUNTS", cuentas):
            sesion_equipo = await admin_auth.create_admin_session(redis, "financial")
            sesion_muni = await admin_auth.create_admin_session(redis, "recoleta")

            self.assertIsNone(await admin_auth.get_admin_session_account(redis, sesion_equipo))
            self.assertEqual(
                (await admin_auth.get_admin_session_account(redis, sesion_equipo, allow_equipo=True))["comuna"],
                "general",
            )
            self.assertEqual((await admin_auth.get_admin_session_account(redis, sesion_muni))["comuna"], "Recoleta")
            self.assertEqual(admin_auth.comunas_municipales(), ["Recoleta"])

    def test_login_del_equipo_redirige_a_documentos(self):
        app = FastAPI()
        app.state.redis = FakeRedis()
        app.include_router(admin.router)
        client = TestClient(app, follow_redirects=False)
        with patch.object(admin, "authenticate_admin", return_value=EQUIPO):
            response = client.post("/admin/login", data={"username": "financial", "password": "x"})
        self.assertEqual(response.headers["location"], "/admin/documentos")


if __name__ == "__main__":
    unittest.main()
