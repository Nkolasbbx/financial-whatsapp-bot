import json
import unittest
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
        self.client = TestClient(app, follow_redirects=False)
        self.client.cookies.set("financial_admin_session", "sesion-1")

        for target, new in (
            ("get_admin_session_account", AsyncMock(return_value=ACCOUNT)),
            ("list_recent_jobs", lambda *a, **k: []),
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


if __name__ == "__main__":
    unittest.main()
