import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

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


class VisibleHitoContextTests(unittest.TestCase):
    def test_formats_hito_rubro_and_comuna_for_the_user(self):
        result = ia._format_visible_hito_context({
            "title": "Solicitar resolución sanitaria",
            "description": "Completar la solicitud ante la SEREMI.",
            "rubro": "alimentos",
            "comuna": "Recoleta",
        })

        self.assertIn("*Hito:* Solicitar resolución sanitaria", result)
        self.assertIn("*Rubro:* alimentos", result)
        self.assertIn("*Comuna:* Recoleta", result)

    def test_without_hito_context_returns_empty_text(self):
        self.assertEqual(ia._format_visible_hito_context(None), "")


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


class LlamarLlmReasoningTests(unittest.TestCase):
    def _call(self, **kwargs):
        response = MagicMock()
        response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        with patch.object(ia.httpx, "post", return_value=response) as post_mock:
            texto = ia.llamar_llm(
                messages=[{"role": "user", "content": "hola"}],
                ollama_url="https://openrouter.ai/api/v1",
                ollama_model="anthropic/claude-haiku-5.5",
                ia_api_key="sk-or-test",
                **kwargs,
            )
        return texto, post_mock.call_args.kwargs["json"]

    def test_disable_reasoning_sends_openrouter_parameter(self):
        texto, payload = self._call(disable_reasoning=True)

        self.assertEqual(texto, "ok")
        self.assertEqual(payload["reasoning"], {"enabled": False})
        self.assertEqual(payload["model"], "anthropic/claude-haiku-5.5")

    def test_reasoning_parameter_is_omitted_by_default(self):
        _, payload = self._call()

        self.assertNotIn("reasoning", payload)


HISTORIAL = [
    {"role": "user", "content": "¿Cuánto cuesta la patente en Recoleta?"},
    {"role": "assistant", "content": "Depende del capital propio declarado."},
]


class FakeSupabase:
    def __init__(self):
        self.updates = []

    def table(self, name):
        self.table_name = name
        return self

    def update(self, payload):
        self.updates.append(payload)
        return self

    def eq(self, column, value):
        return self

    def execute(self):
        return None


class ActualizarResumenConversacionTests(unittest.TestCase):
    def _patches(self, historial=HISTORIAL, user=None, res_key="sk-or-test", llm_text="Temas principales:\n- Patente"):
        self.llm_mock = MagicMock(return_value=llm_text)
        self.supabase = FakeSupabase()
        return (
            patch.object(ia, "get_messages", return_value=historial),
            patch.object(ia, "get_user", return_value=user or {}),
            patch.object(ia, "llamar_llm", self.llm_mock),
            patch.object(ia, "RES_URL", "https://openrouter.ai/api/v1"),
            patch.object(ia, "RES_MODEL", "openai/gpt-4o-mini"),
            patch.object(ia, "RES_KEY", res_key),
            patch.object(dependencies, "supabase", self.supabase),
        )

    def test_prompt_combines_previous_summary_messages_and_required_sections(self):
        user = {"resumen_conversacion": "Temas principales:\n- Inicio de actividades en el SII"}
        with ExitStack() as stack:
            for p in self._patches(user=user):
                stack.enter_context(p)
            resumen = ia.actualizar_resumen_conversacion("56911111111")

        self.assertEqual(resumen, "Temas principales:\n- Patente")
        prompt = self.llm_mock.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Inicio de actividades en el SII", prompt)
        self.assertIn("Usuario: ¿Cuánto cuesta la patente en Recoleta?", prompt)
        self.assertIn("Asistente: Depende del capital propio declarado.", prompt)
        for seccion in (
            "Temas principales:",
            "Consultas realizadas:",
            "Respuestas relevantes entregadas:",
            "Decisiones del usuario:",
            "Pendientes:",
        ):
            self.assertIn(seccion, prompt)
        self.assertIn("Prohibido inventar", prompt)

    def test_uses_openrouter_summary_model_and_saves_summary(self):
        with ExitStack() as stack:
            for p in self._patches():
                stack.enter_context(p)
            ia.actualizar_resumen_conversacion("56911111111")

        kwargs = self.llm_mock.call_args.kwargs
        self.assertEqual(kwargs["ollama_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(kwargs["ollama_model"], "openai/gpt-4o-mini")
        self.assertEqual(kwargs["ia_api_key"], "sk-or-test")
        self.assertEqual(self.supabase.table_name, "users")
        self.assertEqual(
            self.supabase.updates,
            [{"resumen_conversacion": "Temas principales:\n- Patente"}],
        )

    def test_without_history_returns_none_without_calling_model(self):
        with ExitStack() as stack:
            for p in self._patches(historial=[]):
                stack.enter_context(p)
            resumen = ia.actualizar_resumen_conversacion("56911111111")

        self.assertIsNone(resumen)
        self.llm_mock.assert_not_called()

    def test_without_summary_credentials_skips_model(self):
        with ExitStack() as stack:
            for p in self._patches(res_key=None):
                stack.enter_context(p)
            resumen = ia.actualizar_resumen_conversacion("56911111111")

        self.assertIsNone(resumen)
        self.llm_mock.assert_not_called()
        self.assertEqual(self.supabase.updates, [])

    def test_empty_model_response_does_not_overwrite_summary(self):
        with ExitStack() as stack:
            for p in self._patches(llm_text=""):
                stack.enter_context(p)
            resumen = ia.actualizar_resumen_conversacion("56911111111")

        self.assertIsNone(resumen)
        self.assertEqual(self.supabase.updates, [])


class DebeActualizarResumenTests(unittest.TestCase):
    def test_triggers_when_crossing_configured_frequency(self):
        with patch.object(ia, "RESUMEN_FRECUENCIA_MENSAJES", 10):
            self.assertTrue(ia._debe_actualizar_resumen(10))
            # Total impar (otro flujo guardó un mensaje suelto): igual cruza.
            self.assertTrue(ia._debe_actualizar_resumen(11))
            self.assertFalse(ia._debe_actualizar_resumen(12))
            self.assertFalse(ia._debe_actualizar_resumen(8))

    def test_respects_custom_frequency(self):
        with patch.object(ia, "RESUMEN_FRECUENCIA_MENSAJES", 4):
            self.assertTrue(ia._debe_actualizar_resumen(4))
            self.assertFalse(ia._debe_actualizar_resumen(6))
            self.assertTrue(ia._debe_actualizar_resumen(8))


class GetAiResponseMemoriaTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, total_mensajes):
        user = {
            "phone": "56911111111",
            "comuna": "Recoleta",
            "rubro": "Panadería",
            "resumen_conversacion": "Pendientes:\n- Averiguar costo de la patente",
        }
        self.llm_mock = MagicMock(return_value="Respuesta")
        self.background_mock = MagicMock()
        self.get_messages_mock = MagicMock(return_value=HISTORIAL)
        with (
            patch.object(ia, "requiere_rag", AsyncMock(return_value=(False, None))),
            patch.object(ia, "get_messages", self.get_messages_mock),
            patch.object(ia, "llamar_llm", self.llm_mock),
            patch.object(ia, "save_message"),
            patch.object(ia, "contar_mensajes", return_value=total_mensajes),
            patch.object(ia, "RESUMEN_FRECUENCIA_MENSAJES", 10),
            patch.object(ia, "IA_DISABLE_REASONING", True),
            patch.object(ia, "actualizar_resumen_en_background", self.background_mock),
        ):
            await ia.get_ai_response(user, "¿Y lo de la patente?", True)

    async def test_uses_saved_summary_and_recent_messages_as_context(self):
        await self._run(total_mensajes=4)

        messages = self.llm_mock.call_args.kwargs["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Averiguar costo de la patente", messages[0]["content"])
        self.assertEqual(messages[1:3], HISTORIAL)
        self.assertEqual(messages[-1], {"role": "user", "content": "¿Y lo de la patente?"})
        self.get_messages_mock.assert_called_once_with("56911111111", limit=10)
        self.assertTrue(self.llm_mock.call_args.kwargs["disable_reasoning"])
        self.background_mock.assert_not_called()

    async def test_triggers_summary_update_when_frequency_is_reached(self):
        await self._run(total_mensajes=10)

        self.background_mock.assert_called_once_with("56911111111", None)


if __name__ == "__main__":
    unittest.main()
