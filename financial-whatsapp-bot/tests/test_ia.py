import unittest
from unittest.mock import AsyncMock, patch

import dependencies
import core.ia as ia

DOCUMENTAL_VECTOR = [1.0, 0.0]
NO_DOCUMENTAL_VECTOR = [0.0, 1.0]


def fake_embedding_factory(message_vector):
    async def fake_embedding(texto, prefix="query"):
        if texto in ia.EJEMPLOS_DOCUMENTALES:
            return DOCUMENTAL_VECTOR
        if texto in ia.EJEMPLOS_NO_DOCUMENTALES:
            return NO_DOCUMENTAL_VECTOR
        return message_vector

    return fake_embedding


class RequiereRagTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ia._cache_embeddings_ejemplos = None

    async def test_keyword_match_returns_true_without_computing_vector(self):
        usa_rag, vector = await ia.requiere_rag("¿Cuánto cuesta la patente comercial?")

        self.assertTrue(usa_rag)
        self.assertIsNone(vector)

    async def test_conversational_message_returns_false(self):
        usa_rag, vector = await ia.requiere_rag("hola")

        self.assertFalse(usa_rag)
        self.assertIsNone(vector)

    async def test_ambiguous_message_close_to_documental_examples(self):
        with patch.object(ia, "obtener_embedding_remoto", fake_embedding_factory([0.9, 0.1])):
            usa_rag, vector = await ia.requiere_rag("necesito ayuda con un tema pendiente")

        self.assertTrue(usa_rag)
        self.assertEqual(vector, [0.9, 0.1])

    async def test_ambiguous_message_close_to_non_documental_examples(self):
        with patch.object(ia, "obtener_embedding_remoto", fake_embedding_factory([0.1, 0.9])):
            usa_rag, vector = await ia.requiere_rag("oye cuentame algo random")

        self.assertFalse(usa_rag)
        self.assertIsNone(vector)

    async def test_embedding_failure_falls_back_to_true(self):
        async def failing_embedding(texto, prefix="query"):
            raise RuntimeError("boom")

        with patch.object(ia, "obtener_embedding_remoto", failing_embedding):
            usa_rag, vector = await ia.requiere_rag("mensaje raro sin palabras clave")

        self.assertTrue(usa_rag)
        self.assertIsNone(vector)


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, query, params):
        pass

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return FakeCursor(self._rows)


class FakePool:
    def __init__(self, rows):
        self._rows = rows

    def getconn(self):
        return FakeConn(self._rows)

    def putconn(self, conn):
        pass


class ObtenerContextoRagThresholdTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunks_below_threshold_are_discarded(self):
        rows = [
            ("Contenido A", {"file_name": "doc_a"}, 0.1),  # similarity 0.9 -> pasa
            ("Contenido B", {"file_name": "doc_b"}, 0.5),  # similarity 0.5 -> descartado
        ]
        with (
            patch.object(dependencies, "db_pool", FakePool(rows)),
            patch.object(ia, "RAG_SIMILARITY_THRESHOLD", 0.6),
        ):
            resultado = await ia.obtener_contexto_rag(
                "cuanto cuesta la patente", "recoleta", query_vector=[0.1, 0.2]
            )

        self.assertIn("Contenido A", resultado["contexto"])
        self.assertNotIn("Contenido B", resultado["contexto"])
        self.assertEqual(len(resultado["fuentes"]), 1)

    async def test_all_chunks_below_threshold_reports_no_info(self):
        rows = [
            ("Contenido A", {"file_name": "doc_a"}, 0.7),  # similarity 0.3
            ("Contenido B", {"file_name": "doc_b"}, 0.5),  # similarity 0.5
        ]
        with (
            patch.object(dependencies, "db_pool", FakePool(rows)),
            patch.object(ia, "RAG_SIMILARITY_THRESHOLD", 0.6),
        ):
            resultado = await ia.obtener_contexto_rag(
                "cuanto cuesta la patente", "recoleta", query_vector=[0.1, 0.2]
            )

        self.assertIn("SIN INFORMACIÓN disponible", resultado["contexto"])
        self.assertEqual(resultado["fuentes"], [])

    async def test_reuses_precomputed_vector_without_new_embedding_call(self):
        embedding_mock = AsyncMock()
        with (
            patch.object(dependencies, "db_pool", FakePool([])),
            patch.object(ia, "obtener_embedding_remoto", embedding_mock),
        ):
            await ia.obtener_contexto_rag(
                "cuanto cuesta la patente", "recoleta", query_vector=[0.1, 0.2]
            )

        embedding_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
