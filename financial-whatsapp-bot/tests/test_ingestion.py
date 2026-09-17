import unittest
from unittest.mock import AsyncMock, patch

import httpx

import core.ingestion as ingestion


class ChunkingTests(unittest.TestCase):
    def test_build_parent_child_chunks_groups_by_header_and_nests_children(self):
        text = (
            "# Sección 1\n\n"
            + ("Contenido de prueba. " * 40)
            + "\n\n# Sección 2\n\n"
            + ("Otro contenido distinto. " * 40)
        )

        items = ingestion.build_parent_child_chunks(text)

        self.assertTrue(items)
        headers = {item["header"] for item in items}
        self.assertIn("# Sección 1", headers)
        self.assertIn("# Sección 2", headers)
        for item in items:
            self.assertIn(item["child_text"], item["parent_text"])
            self.assertEqual(len(item["parent_id"]), 16)

    def test_short_text_produces_a_single_chunk(self):
        items = ingestion.build_parent_child_chunks("Texto corto sin encabezados.")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["child_index"], 0)


class ProcessDocumentToRowsTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(ingestion, "INGESTION_ENABLE_CONTEXTUAL", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rows_carry_the_provided_metadata_and_content_hash(self):
        text = "# Trámite\n\n" + ("Información relevante del trámite. " * 30)
        extra_meta = {
            "comuna": "Recoleta",
            "rubros": ["general"],
            "vigencia_desde": None,
            "vigencia_hasta": None,
            "content_hash": "abc123",
            "uploaded_by": "InnovaRecoleta",
            "uploaded_at": "2026-09-16T00:00:00+00:00",
            "source": "Panel admin — InnovaRecoleta",
            "review_flag": False,
        }

        rows = ingestion.process_document_to_rows("tramite.md", text, "markdown", extra_meta)

        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["meta"]["comuna"], "Recoleta")
            self.assertEqual(row["meta"]["content_hash"], "abc123")
            self.assertEqual(row["meta"]["file_name"], "tramite.md")
            self.assertEqual(row["meta"]["file_type"], "markdown")
            self.assertIn("parent_id", row["meta"])
            self.assertIn("child_index", row["meta"])
            # Sin contextual retrieval, embed_text es igual al child_text.
            self.assertEqual(row["embed_text"], row["meta"]["child_text"])

    def test_empty_text_produces_no_rows(self):
        rows = ingestion.process_document_to_rows("vacio.md", "", "markdown", {"comuna": "general"})
        self.assertEqual(rows, [])

    def test_contextual_retrieval_disabled_never_calls_the_llm(self):
        with patch.object(ingestion, "generate_parent_context_map") as context_mock:
            ingestion.process_document_to_rows(
                "doc.md",
                "# H\n\n" + ("texto " * 50),
                "markdown",
                {"comuna": "general"},
            )
        context_mock.assert_not_called()


class GenerateParentContextMapTests(unittest.TestCase):
    def test_returns_empty_when_contextual_disabled(self):
        with patch.object(ingestion, "INGESTION_ENABLE_CONTEXTUAL", False):
            result = ingestion.generate_parent_context_map("doc", "parent text", [
                {"child_index": 0, "child_text": "child"}
            ])
        self.assertEqual(result, {})

    def test_returns_empty_without_children(self):
        with patch.object(ingestion, "INGESTION_ENABLE_CONTEXTUAL", True), \
             patch.object(ingestion, "RES_KEY", "fake-key"):
            result = ingestion.generate_parent_context_map("doc", "parent text", [])
        self.assertEqual(result, {})


class DetectLowTextPagesTests(unittest.TestCase):
    def _build_pdf(self, path, with_big_image: bool, with_text: bool):
        import pymupdf

        doc = pymupdf.open()
        page = doc.new_page(width=600, height=800)
        if with_text:
            page.insert_text((50, 50), "Texto de prueba. " * 60, fontsize=10)
        if with_big_image:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 200))
            pix.set_rect(pix.irect, (200, 200, 200))
            page.insert_image(pymupdf.Rect(20, 100, 580, 780), pixmap=pix)
        doc.save(path)
        doc.close()

    def test_flags_a_page_with_large_image_and_little_text(self):
        import tempfile, os

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "escaneado.pdf")
            self._build_pdf(path, with_big_image=True, with_text=False)

            flagged = ingestion.detect_low_text_pages(path)

        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["page"], 1)

    def test_does_not_flag_a_normal_text_page(self):
        import tempfile, os

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "digital.pdf")
            self._build_pdf(path, with_big_image=False, with_text=True)

            flagged = ingestion.detect_low_text_pages(path)

        self.assertEqual(flagged, [])


class EmbedBatchRemotoTests(unittest.IsolatedAsyncioTestCase):
    @patch.object(ingestion, "INGESTION_EMBEDDING_BATCH_SIZE", 2)
    async def test_splits_into_batches_of_configured_size(self):
        calls = []

        async def fake_batch(textos, prefix="query", timeout=15.0):
            calls.append(list(textos))
            return [[float(len(t))] for t in textos]

        with patch.object(ingestion, "obtener_embeddings_remotos_batch", fake_batch):
            resultado = await ingestion.embed_batch_remoto(["a", "bb", "ccc"], prefix="passage")

        self.assertEqual(len(calls), 2)
        self.assertEqual(len(calls[0]), 2)
        self.assertEqual(len(calls[1]), 1)
        self.assertEqual(resultado, [[1.0], [2.0], [3.0]])

    async def test_retries_on_429_and_eventually_succeeds(self):
        request = httpx.Request("POST", "https://example.test")
        error_response = httpx.Response(429, request=request)
        mock = AsyncMock(side_effect=[
            httpx.HTTPStatusError("rate limited", request=request, response=error_response),
            [[1.0, 2.0]],
        ])

        with patch.object(ingestion, "obtener_embeddings_remotos_batch", mock), \
             patch.object(ingestion.asyncio, "sleep", AsyncMock()):
            resultado = await ingestion.embed_batch_remoto(["texto"], prefix="passage")

        self.assertEqual(resultado, [[1.0, 2.0]])
        self.assertEqual(mock.await_count, 2)

    async def test_gives_up_on_non_retryable_error(self):
        request = httpx.Request("POST", "https://example.test")
        error_response = httpx.Response(500, request=request)
        mock = AsyncMock(side_effect=httpx.HTTPStatusError(
            "server error", request=request, response=error_response
        ))

        with patch.object(ingestion, "obtener_embeddings_remotos_batch", mock):
            with self.assertRaises(httpx.HTTPStatusError):
                await ingestion.embed_batch_remoto(["texto"], prefix="passage")


if __name__ == "__main__":
    unittest.main()
