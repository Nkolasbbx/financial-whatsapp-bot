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


class PersonalizedAssistantContextTests(unittest.TestCase):
    def test_detects_multiple_personal_data_domains(self):
        domains = ia._assistant_data_domains(
            "¿Cuánto me falta para mi meta mensual y qué compromisos tengo en octubre?"
        )

        self.assertIn("finances", domains)
        self.assertIn("calendar", domains)

    def test_detects_fund_domain_for_postulacion_and_roadmap_cost_questions(self):
        self.assertIn(
            "funds",
            ia._assistant_data_domains("¿Qué posibilidades de postulación tengo?"),
        )
        domains = ia._assistant_data_domains("¿Qué hitos me faltan y cuánto cuestan?")
        self.assertIn("roadmap", domains)
        self.assertIn("formalization_costs", domains)

    def test_month_bounds_supports_named_month_and_relative_month(self):
        today = ia.date(2026, 10, 26)

        self.assertEqual(
            ia._month_bounds("mis gastos de septiembre", today=today),
            (ia.date(2026, 9, 1), ia.date(2026, 10, 1), "septiembre de 2026"),
        )
        self.assertEqual(
            ia._month_bounds("el próximo mes", today=today, prefer_future=True),
            (ia.date(2026, 11, 1), ia.date(2026, 12, 1), "noviembre de 2026"),
        )
        self.assertEqual(
            ia._month_bounds("compromisos en septiembre", today=today, prefer_future=True),
            (ia.date(2026, 9, 1), ia.date(2026, 10, 1), "septiembre de 2026"),
        )
        self.assertEqual(
            ia._month_bounds("el próximo septiembre", today=today, prefer_future=True),
            (ia.date(2027, 9, 1), ia.date(2027, 10, 1), "septiembre de 2027"),
        )

    def test_extracts_chilean_clp_goal_formats(self):
        for message, expected in (
            ("Mi meta mensual es llegar a $500.000", 500_000),
            ("Quiero ahorrar 300 mil al mes", 300_000),
            ("Mi meta mensual es 1 millón", 1_000_000),
            ("Mi meta mensual es 2 millones", 2_000_000),
            ("Mi meta mensual es 1,5 millones", 1_500_000),
            ("Mi meta mensual es 1.5 millones", 1_500_000),
            ("Mi meta mensual es $1 millón", 1_000_000),
            ("Mi meta mensual es $1.500.000", 1_500_000),
        ):
            with self.subTest(message=message):
                self.assertEqual(ia._extract_clp_goal(message), expected)
        self.assertIsNone(
            ia._extract_clp_goal("Mi meta para el año 2026 es avanzar"),
        )

    def test_finance_context_calculates_remaining_amount_and_calendar_context(self):
        user = {
            "id": "00000000-0000-0000-0000-000000000001",
            "roadmap": [],
        }
        message = (
            "Mi meta mensual es $500.000, ¿cuánto me falta según mis ingresos "
            "y gastos y qué compromisos tengo en octubre?"
        )
        event = {
            "event_at": "2026-10-28T15:00:00+00:00",
            "description": "Reunión con proveedor",
            "status": "active",
        }
        with (
            patch.object(ia, "_local_today", return_value=ia.date(2026, 10, 26)),
            patch.object(
                ia,
                "get_financial_month_summary",
                return_value={
                    "income_total": 80_000,
                    "expense_total": 20_000,
                    "net_total": 60_000,
                    "movement_count": 3,
                },
            ) as summary_mock,
            patch.object(ia, "get_calendar_events_between", return_value=[event]) as events_mock,
        ):
            context = ia._build_personalized_context(user, message)

        summary_mock.assert_called_once_with(
            user["id"], ia.date(2026, 10, 1), ia.date(2026, 11, 1),
        )
        events_mock.assert_called_once()
        self.assertIn("Monto faltante para alcanzarla: $440.000", context)
        self.assertIn("Reunión con proveedor", context)
        self.assertIn("28/10/2026 12:00", context)
        self.assertIn("octubre de 2026", context)

    def test_formalization_cost_context_uses_saved_items_and_assumptions(self):
        user = {"id": "00000000-0000-0000-0000-000000000001"}
        saved_context = {
            "diagnostic": {
                "workflow_status": "confirmed",
                "period_start": "2026-09-01",
                "result": {
                    "initial_remaining_min": 100_000,
                    "initial_remaining_max": 150_000,
                    "assumptions": ["estimación según antecedentes declarados"],
                },
                "missing_fields": ["amount:patente"],
            },
            "items": [{
                "item_key": "patente",
                "label": "Patente municipal",
                "amount_clp_min": 100_000,
                "amount_clp_max": 150_000,
                "frequency": "annual",
                "applicability": "applies",
                "included_in_baseline": False,
            }],
        }
        with patch.object(ia, "get_formalization_context", return_value=saved_context):
            context = ia._formalization_cost_context(user)

        self.assertIn("costo inicial pendiente mínimo: $100.000", context)
        self.assertIn("Patente municipal: $100.000–$150.000", context)
        self.assertIn("estimación según antecedentes declarados", context)
        self.assertIn("amount:patente", context)

    def test_formalization_cost_context_calculates_known_catalog_cost_for_pending_hito(self):
        from tests.test_formalization_estimates import small_catalog

        user = {
            "id": "00000000-0000-0000-0000-000000000001",
            "rubro": "alimentos",
            "comuna": "Recoleta",
            "inicio_sii": "no",
            "location_type": "local",
            "roadmap": [{
                "title": "Inicio de Actividades en el SII",
                "done": False,
            }],
        }
        with (
            patch.object(
                ia,
                "get_formalization_context",
                return_value={"diagnostic": None, "items": []},
            ),
            patch.object(
                ia,
                "get_formalization_estimate_catalog",
                return_value={
                    "version": "test-v1",
                    "review_by": "2026-11-16",
                    "document": small_catalog(),
                },
            ),
            patch.object(ia, "_local_today", return_value=ia.date(2026, 10, 26)),
        ):
            context = ia._formalization_cost_context(
                user,
                "¿Cuánto cuesta el inicio de actividades?",
            )

        self.assertIn("Inicio de Actividades en el SII", context)
        self.assertIn("Estimaciones orientativas del catálogo activo test-v1", context)
        self.assertIn("C01: concepto C01: $0 (once)", context)
        self.assertIn("no equivalen a cotizaciones ni al costo total", context)

    def test_formalization_cost_context_requests_location_before_estimating(self):
        from tests.test_formalization_estimates import small_catalog

        user = {
            "id": "00000000-0000-0000-0000-000000000001",
            "rubro": "alimentos",
            "comuna": "Recoleta",
            "inicio_sii": "no",
            "roadmap": [{
                "title": "Solicitar patente municipal",
                "done": False,
            }],
        }
        with (
            patch.object(
                ia,
                "get_formalization_context",
                return_value={"diagnostic": None, "items": []},
            ),
            patch.object(
                ia,
                "get_formalization_estimate_catalog",
                return_value={
                    "version": "test-v1",
                    "review_by": "2026-11-16",
                    "document": small_catalog(),
                },
            ),
            patch.object(ia, "_local_today", return_value=ia.date(2026, 10, 26)),
        ):
            context = ia._formalization_cost_context(
                user,
                "¿Cuánto me cuesta formalizar?",
            )

        self.assertIn("Solicitar patente municipal", context)
        self.assertIn("falta saber si el negocio funcionará en vivienda, local u otra modalidad", context)
        self.assertNotIn("Estimaciones orientativas del catálogo activo", context)

    def test_formalization_cost_context_uses_answers_to_estimate_related_hito(self):
        from tests.test_formalization_estimates import small_catalog

        user = {
            "id": "00000000-0000-0000-0000-000000000001",
            "rubro": "alimentos",
            "comuna": "Recoleta",
            "inicio_sii": "no",
            "roadmap": [{
                "title": "Resolución Sanitaria (si aplica)",
                "done": False,
            }],
        }
        diagnostic = {
            "workflow_status": "draft",
            "period_start": "2026-09-01",
            "location_type": "local",
            "profile_snapshot": {
                "rubro": "alimentos",
                "comuna": "Recoleta",
                "inicio_sii": "no",
            },
            "answers": {
                "estimate_facts": {
                    "subtype": "alimentos_preparacion",
                    "small_installation": True,
                    "capital_under_10m": True,
                    "missing_property_documents": False,
                    "adaptations": "none",
                    "missing_components": {},
                    "new_operating_costs_not_in_baseline": False,
                    "municipal_patent_required": False,
                    "waste_charge_required": False,
                },
            },
        }
        with (
            patch.object(
                ia,
                "get_formalization_context",
                return_value={"diagnostic": diagnostic, "items": []},
            ),
            patch.object(
                ia,
                "get_formalization_estimate_catalog",
                return_value={
                    "version": "test-v1",
                    "review_by": "2026-11-16",
                    "document": small_catalog(),
                },
            ),
            patch.object(ia, "_local_today", return_value=ia.date(2026, 10, 26)),
        ):
            context = ia._formalization_cost_context(
                user,
                "¿Cuánto cuesta la resolución sanitaria?",
            )

        self.assertIn("C07: concepto C07: $30–$40 (once)", context)
        self.assertIn("fuentes del catálogo", context)

    def test_finance_context_does_not_treat_missing_movements_as_zero_progress(self):
        user = {"id": "00000000-0000-0000-0000-000000000001"}
        with (
            patch.object(ia, "_local_today", return_value=ia.date(2026, 10, 26)),
            patch.object(
                ia,
                "get_financial_month_summary",
                return_value={
                    "income_total": 0,
                    "expense_total": 0,
                    "net_total": 0,
                    "movement_count": 0,
                },
            ),
        ):
            context = ia._financial_context(user, "Mi meta mensual es $500.000")

        self.assertIn("no es posible calcular el progreso", context)
        self.assertNotIn("Monto faltante para alcanzarla", context)

    def test_finance_context_does_not_treat_missing_summary_fields_as_zero(self):
        user = {"id": "00000000-0000-0000-0000-000000000001"}
        with (
            patch.object(ia, "_local_today", return_value=ia.date(2026, 10, 26)),
            patch.object(
                ia,
                "get_financial_month_summary",
                return_value={
                    "expense_total": 10_000,
                    "net_total": None,
                    "movement_count": 2,
                },
            ),
        ):
            context = ia._financial_context(user, "Mi meta mensual es $500.000")

        self.assertIn("está incompleto", context)
        self.assertNotIn("Ingresos confirmados: $0", context)
        self.assertNotIn("Monto faltante para alcanzarla", context)

    def test_calendar_context_queries_next_week_when_period_is_named(self):
        user = {"id": "00000000-0000-0000-0000-000000000001"}
        with (
            patch.object(ia, "_local_today", return_value=ia.date(2026, 10, 26)),
            patch.object(ia, "get_calendar_events_between", return_value=[]) as events_mock,
        ):
            context = ia._calendar_context(user, "¿Qué compromisos tengo la próxima semana?")

        args = events_mock.call_args.args
        self.assertEqual(args[1].date(), ia.date(2026, 11, 2))
        self.assertEqual(args[2].date(), ia.date(2026, 11, 9))
        self.assertIn("semana del 2026-11-02", context)

    def test_fund_context_reports_evaluated_requirements_and_missing_data(self):
        user = {
            "id": "00000000-0000-0000-0000-000000000001",
            "inicio_sii": "no",
            "rubro": "textil",
        }
        funds = [{
            "nombre": "Capital Semilla Emprende",
            "fecha_cierre": ia.date(2026, 12, 1),
            "monto_max": 3_500_000,
            "activo": True,
            "requisitos": [
                {"texto": "Persona mayor de edad", "clave": "mayor_edad"},
                {
                    "texto": "Ventas anuales dentro del rango",
                    "clave": "ventas_semilla",
                    "recomendacion": "Confirma tus ventas del último año.",
                },
            ],
        }]
        with (
            patch.object(ia, "_local_today", return_value=ia.date(2026, 10, 26)),
            patch.object(ia, "get_available_fund_candidates", return_value=funds),
            patch.object(ia, "get_requirement_definitions", return_value={}),
            patch.object(
                ia,
                "get_fund_answer_records",
                return_value={"ventas_semilla": ia.UNKNOWN_ANSWER},
            ),
        ):
            context = ia._funds_context(
                user,
                "¿Qué cumplo para postular a Capital Semilla?",
            )

        self.assertIn("Capital Semilla Emprende", context)
        self.assertIn("1 cumplen, 0 no cumplen, 1 por confirmar", context)
        self.assertIn("cumple: Persona mayor de edad", context)
        self.assertIn("por confirmar: Ventas anuales dentro del rango", context)
        self.assertIn("Confirma tus ventas del último año", context)

    def test_personalized_context_does_not_duplicate_milestones_with_costs(self):
        with (
            patch.object(ia, "_roadmap_context", return_value="roadmap duplicado") as roadmap,
            patch.object(ia, "_formalization_cost_context", return_value="hitos y costos") as costs,
        ):
            context = ia._build_personalized_context(
                {"id": "00000000-0000-0000-0000-000000000001"},
                "¿Qué hitos me faltan y cuánto cuestan?",
            )

        self.assertEqual(context.count("hitos y costos"), 1)
        self.assertNotIn("roadmap duplicado", context)
        roadmap.assert_not_called()
        costs.assert_called_once()

    def test_fund_context_reports_when_results_are_truncated(self):
        user = {"id": "00000000-0000-0000-0000-000000000001"}
        funds = [
            {"nombre": f"Fondo {index}", "fecha_cierre": None, "monto_max": None}
            for index in range(6)
        ]
        evaluations = [
            {
                "fund": fund,
                "met": 0,
                "failed": 0,
                "unknown": 0,
                "requirements": [],
            }
            for fund in funds
        ]
        with (
            patch.object(ia, "_local_today", return_value=ia.date(2026, 10, 26)),
            patch.object(ia, "get_available_fund_candidates", return_value=funds),
            patch.object(ia, "get_requirement_definitions", return_value={}),
            patch.object(ia, "get_fund_answer_records", return_value={}),
            patch.object(ia, "evaluate_available_funds", return_value=evaluations),
        ):
            context = ia._funds_context(user, "¿Qué fondos vigentes tengo?")

        self.assertIn("Se muestran 5 de 6 fondos", context)
        self.assertNotIn("Fondo 5:", context)

    def test_database_failure_is_explicit_instead_of_being_treated_as_zero(self):
        user = {"id": "00000000-0000-0000-0000-000000000001"}
        with patch.object(
            ia,
            "get_financial_month_summary",
            side_effect=RuntimeError("storage unavailable"),
        ):
            context = ia._build_personalized_context(user, "¿Cómo va mi meta mensual?")

        self.assertIn("no fue posible consultar los datos guardados", context)
        self.assertIn("No asumir que están vacíos", context)

    def test_invalid_personal_data_shape_returns_partial_context(self):
        def load_invalid_data():
            raise ValueError("invalid event date")

        context = ia._personal_data_section(
            "Calendario",
            load_invalid_data,
        )

        self.assertIn("formato inválido", context)
        self.assertIn("otras secciones disponibles", context)


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
            patch.object(
                ia,
                "_build_personalized_context",
                return_value="[DATOS PERSONALES Y FUNCIONALIDADES DISPONIBLES]\ncontexto verificado",
            ),
        ):
            await ia.get_ai_response(user, "¿Y lo de la patente?", True)

    async def test_uses_saved_summary_and_recent_messages_as_context(self):
        await self._run(total_mensajes=4)

        messages = self.llm_mock.call_args.kwargs["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Averiguar costo de la patente", messages[0]["content"])
        self.assertIn("contexto verificado", messages[0]["content"])
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
