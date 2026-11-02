"""Offline tests: observers do not send requests or change business results."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from scripts import performance_report as report
from services import performance as perf


class FakeQuery:
    def __init__(self, response=None):
        self.response = response or SimpleNamespace(data=[{"phone": "+56911111111"}])
        self.calls = []

    def select(self, *args, **kwargs):
        self.calls.append(("select", args, kwargs))
        return self

    def eq(self, *args, **kwargs):
        self.calls.append(("eq", args, kwargs))
        return self

    def execute(self):
        return self.response


def record(**changes):
    return {
        "schema": 1, "trace_id": "a" * 32, "run": "baseline", "case": "menu",
        "input_type": "text", "phase": "web", "attempt": 1,
        "elapsed_ms": 110, "last_response_ms": 100, "first_response_ms": 100,
        "accepted_sends": 1, "outcome": "completed", "recorded_at": "2026-10-27T12:00:00Z",
        "stages": {}, "db_operations": {}, "clock_warning": False,
        **changes,
    }


class PerformanceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "PERFORMANCE_METRICS_ENABLED": "true", "PERFORMANCE_RUN_LABEL": "baseline_test",
            "PERFORMANCE_OUTPUT_DIR": "",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    async def test_context_crosses_to_thread_and_is_reset(self):
        query = FakeQuery()
        client = perf.instrument_supabase(SimpleNamespace(table=lambda name: query))
        with patch.object(perf, "_emit"):
            with perf.trace_scope(perf.Trace()) as trace:
                result = await asyncio.to_thread(lambda: client.table("users").select("*").eq("phone", "+56911111111").execute())
                self.assertIs(result, query.response)
                self.assertEqual(trace.db_operations, {"table:users": 1})
                self.assertEqual(trace.stages["supabase"]["calls"], 1)
                self.assertEqual(trace.stages["supabase.table:users"]["calls"], 1)
        self.assertIsNone(perf._current.get())

    def test_disabled_supabase_is_identical(self):
        client = object()
        with patch.dict(os.environ, {"PERFORMANCE_METRICS_ENABLED": "false"}):
            self.assertIs(perf.instrument_supabase(client), client)

    def test_proxy_retains_chain_and_rpc_arguments_without_logging_them(self):
        query = FakeQuery()
        raw = Mock()
        raw.rpc.return_value = query
        client = perf.instrument_supabase(raw)
        self.assertIs(perf.instrument_supabase(client), client)
        with patch.object(perf, "_emit"):
            with perf.trace_scope(perf.Trace()) as trace:
                result = client.rpc("check_message_rate_limit", {"p_phone": "+56911111111"}).execute()
        self.assertIs(result, query.response)
        raw.rpc.assert_called_once_with("check_message_rate_limit", {"p_phone": "+56911111111"})
        self.assertEqual(trace.db_operations, {"rpc:check_message_rate_limit": 1})
        serialized = json.dumps(trace.record())
        self.assertNotIn("+56911111111", serialized)
        self.assertNotIn("p_phone", serialized)

    async def test_concurrent_traces_do_not_mix(self):
        async def run(case):
            with perf.trace_scope(perf.Trace()) as trace:
                perf.set_case(case)
                with perf.span("operation"):
                    await asyncio.sleep(0)
                return trace
        with patch.object(perf, "_emit"):
            a, b = await asyncio.gather(run("menu"), run("funds"))
        self.assertEqual((a.case, b.case), ("menu", "funds"))
        self.assertNotEqual(a.trace_id, b.trace_id)
        self.assertEqual(a.stages["operation"]["calls"], 1)

    def test_exceptions_are_not_swallowed_or_replaced(self):
        error = RuntimeError("private failure details")
        @perf.measure("test")
        def fail():
            raise error
        with patch.object(perf, "_emit"):
            with self.assertRaises(RuntimeError) as raised:
                with perf.trace_scope(perf.Trace()) as trace:
                    fail()
        self.assertIs(raised.exception, error)
        self.assertEqual(trace.outcome, "failed")
        self.assertEqual(trace.stages["test"]["errors"], 1)
        self.assertNotIn("private failure", json.dumps(trace.record()))

    def test_advisor_route_is_measured_as_ai_case(self):
        for result in ("__AI_QUERY__", "__AI_QUERY_WITH_CONTEXT__", "__AI_QUERY_WITH_REFORMULATE__"):
            route = perf.observe_route(lambda result=result: result)
            with patch.object(perf, "_emit"):
                with perf.trace_scope(perf.Trace()) as trace:
                    self.assertEqual(route(), result)
            self.assertFalse(trace.excluded)
            self.assertEqual(trace.case, "ai")

    async def test_ai_queue_links_phases_without_personal_data(self):
        with patch.object(perf, "_emit"):
            with perf.trace_scope(perf.Trace()) as web:
                perf.set_case("ai")
                kwargs = perf.ai_queue_kwargs()
        self.assertEqual(web.outcome, "queued")

        @perf.observe_ai_job
        async def job(ctx, phone, message, performance_context=None):
            with perf.span("ai_llm"):
                pass
            perf._current.get().accepted()
            return "sent"
        with patch.object(perf, "_emit") as emit:
            self.assertEqual(await job({"job_try": 1}, "56911111111", "private question", **kwargs), "sent")
        worker = emit.call_args.args[0]
        self.assertEqual((worker.trace_id, worker.phase, worker.case), (web.trace_id, "worker", "ai"))
        self.assertIn("ai_llm", worker.stages)
        serialized = json.dumps(worker.record())
        for private in ("56911111111", "private question"):
            self.assertNotIn(private, serialized)

    async def test_loop_lag_probe_logs_only_when_enabled(self):
        with patch.object(perf, "_write_line") as write, patch("time.perf_counter", side_effect=[0.0, 0.3, 1.0, 1.0]):
            task = asyncio.ensure_future(perf.monitor_loop_lag(interval=0))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            task.cancel()
        row = json.loads(write.call_args_list[0].args[0])
        self.assertEqual(row["kind"], "loop_lag")
        self.assertGreater(row["lag_ms"], 50)
        with patch.dict(os.environ, {"PERFORMANCE_METRICS_ENABLED": "false"}):
            self.assertIsNone(await perf.monitor_loop_lag())

    def test_report_keeps_loop_lag_apart_from_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics-1.jsonl"
            lag = {"schema": 1, "kind": "loop_lag", "run": "baseline", "pid": 1, "lag_ms": 820.0, "recorded_at": "x"}
            path.write_text(json.dumps(record()) + "\n" + json.dumps(lag) + "\n", encoding="utf-8")
            records, malformed = report.read_records([directory], "baseline")
            self.assertEqual((len(records), malformed), (1, 0))
            self.assertEqual(report.read_loop_lag([directory], "baseline"), [820.0])
            self.assertIn("820.0", report.loop_lag_section([820.0]))

    async def test_meta_send_preserves_result_and_marks_acceptance(self):
        response = {"messages": [{"id": "wamid.secret"}]}
        call = AsyncMock(return_value=response)
        wrapped = perf.observe_meta_send(call)
        with patch.object(perf, "_emit"):
            with perf.trace_scope(perf.Trace()) as trace:
                self.assertIs(await wrapped({"to": "private"}), response)
        self.assertEqual(trace.accepted_sends, 1)
        self.assertIsNotNone(trace.last_response_ms)
        self.assertNotIn("wamid.secret", json.dumps(trace.record()))

    async def test_meta_send_failure_does_not_look_successful(self):
        wrapped = perf.observe_meta_send(AsyncMock(side_effect=RuntimeError("send failed")))
        with patch.object(perf, "_emit"):
            with self.assertRaises(RuntimeError):
                with perf.trace_scope(perf.Trace()) as trace:
                    await wrapped({})
        self.assertEqual(trace.accepted_sends, 0)
        self.assertEqual(trace.outcome, "failed")

    async def test_webhook_measures_without_changing_reply(self):
        request = SimpleNamespace(body=AsyncMock(return_value=json.dumps({
            "entry": [{"changes": [{"value": {"messages": [{"type": "text", "text": {"body": "private"}}]}}]}],
        }).encode()))
        @perf.observe_webhook
        async def handler(request):
            perf.set_case("menu")
            return {"status": "received"}
        with patch.object(perf, "_emit") as emit:
            self.assertEqual(await handler(request), {"status": "received"})
        self.assertEqual(emit.call_args.args[0].case, "menu")
        self.assertNotIn("private", json.dumps(emit.call_args.args[0].record()))

    async def test_disabled_webhook_does_not_read_body(self):
        request = SimpleNamespace(body=AsyncMock())
        handler = AsyncMock(return_value=42)
        with patch.dict(os.environ, {"PERFORMANCE_METRICS_ENABLED": "false"}):
            self.assertEqual(await perf.observe_webhook(handler)(request), 42)
        request.body.assert_not_called()

    async def test_status_callbacks_and_batches_are_not_response_samples(self):
        for messages in ([], [{"type": "text"}, {"type": "text"}]):
            request = SimpleNamespace(body=AsyncMock(return_value=json.dumps({
                "entry": [{"changes": [{"value": {"messages": messages}}]}],
            }).encode()))
            with patch.object(perf, "_emit") as emit:
                await perf.observe_webhook(AsyncMock(return_value={}))(request)
            emit.assert_not_called()

    async def test_financial_queue_links_phases_without_personal_data(self):
        with patch.object(perf, "_emit"):
            with perf.trace_scope(perf.Trace()) as web:
                perf.set_case("financial_parse")
                kwargs = perf.financial_queue_kwargs()
        self.assertEqual(web.outcome, "queued")
        self.assertEqual(set(kwargs["performance_context"]), {"trace_id", "run", "started_at", "enqueued_at", "input_type"})
        @perf.observe_financial_job
        async def job(ctx, phone, message, performance_context=None):
            perf._current.get().accepted()
            return 42
        with patch.object(perf, "_emit") as emit:
            self.assertEqual(await job({"job_try": 2}, "private", "private", **kwargs), 42)
        worker = emit.call_args.args[0]
        self.assertEqual(worker.trace_id, web.trace_id)
        self.assertEqual(worker.phase, "worker")
        self.assertEqual(worker.attempt, 2)
        self.assertEqual(worker.outcome, "completed")

    async def test_missing_or_invalid_job_metadata_does_not_fail_job(self):
        @perf.observe_financial_job
        async def job(ctx, performance_context=None):
            return "unchanged"
        for value in (None, {}, {"trace_id": "bad"}):
            self.assertEqual(await job({}, performance_context=value), "unchanged")

    def test_jsonl_export_and_io_failure_are_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"PERFORMANCE_OUTPUT_DIR": directory}):
                perf._emit(perf.Trace())
            files = list(Path(directory).glob("*.jsonl"))
            self.assertEqual(len(files), 1)
            self.assertEqual(json.loads(files[0].read_text())["schema"], 1)
            with patch.dict(os.environ, {"PERFORMANCE_OUTPUT_DIR": directory}):
                with patch.object(Path, "mkdir", side_effect=PermissionError):
                    perf._emit(perf.Trace())  # must not raise

    def test_disabled_queue_has_no_extra_job_parameters(self):
        self.assertEqual(perf.financial_queue_kwargs(), {})


class ReportTests(unittest.TestCase):
    def test_nearest_rank_p95_and_median(self):
        rows = [record(trace_id=str(index), last_response_ms=index) for index in range(1, 21)]
        result = report.aggregate(report.samples_from_records(rows))[("menu", "text")]
        self.assertEqual(result["median_ms"], 10.5)
        self.assertEqual(result["p95_ms"], 19)

    def test_web_and_worker_are_one_sample_and_keep_all_db_operations(self):
        rows = [record(case="financial_parse", outcome="queued", db_operations={"table:users": 2}),
                record(case="financial_parse", phase="worker", last_response_ms=500, db_operations={"table:users": 1})]
        samples = report.samples_from_records(rows)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["response_ms"], 500)
        self.assertEqual(sum(samples[0]["operations"].values()), 3)

    def test_incomplete_failed_clock_warning_and_no_reply_do_not_improve_median(self):
        rows = [record(trace_id="good"), record(trace_id="queued", outcome="queued"),
                record(trace_id="failed", outcome="failed"),
                record(trace_id="clock", clock_warning=True),
                record(trace_id="silent", outcome="no_reply"),
                record(trace_id="recovered", stages={"supabase": {"ms": 2, "errors": 1}})]
        result = report.aggregate(report.samples_from_records(rows))[("menu", "text")]
        self.assertEqual(result["samples"], 6)
        self.assertEqual(result["valid"], 1)
        self.assertEqual(result["median_ms"], 100)
        self.assertEqual(result["errors"], 2)

    def test_worker_without_web_is_flagged_incomplete(self):
        rows = [record(phase="worker")]
        result = report.aggregate(report.samples_from_records(rows))[("menu", "text")]
        self.assertEqual(result["valid"], 0)
        self.assertEqual(result["pending"], 1)

    def test_retries_not_counted_as_independent_fast_answers(self):
        rows = [record(outcome="queued"), record(phase="worker", outcome="failed"),
                record(phase="worker", attempt=2, last_response_ms=500)]
        result = report.aggregate(report.samples_from_records(rows))[("menu", "text")]
        self.assertEqual(result["samples"], 1)
        self.assertEqual(result["retries"], 1)

    def test_reader_accepts_exported_logs_deduplicates_and_filters_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.log"
            row = record()
            path.write_text("unrelated\n" + perf.PREFIX + json.dumps(row) + "\n" + json.dumps(row) + "\n" + perf.PREFIX + "broken\n", encoding="utf-8")
            rows, malformed = report.read_records([str(path)], "baseline")
            self.assertEqual(len(rows), 1)
            self.assertEqual(malformed, 1)
            self.assertEqual(report.read_records([str(path)], "after")[0], [])

    def test_comparison_uses_real_inputs_and_calculates_reduction(self):
        text = report.comparison([record(last_response_ms=100)], [record(run="after", last_response_ms=75)])
        self.assertIn("25.0%", text)
        self.assertIn("100.0", text)
        self.assertIn("75.0", text)

    def test_report_writes_via_cli_without_external_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.jsonl"
            output = Path(directory) / "report.md"
            path.write_text(json.dumps(record()) + "\n", encoding="utf-8")
            report.main([str(path), "--output", str(output)])
            text = output.read_text(encoding="utf-8")
            self.assertIn("Línea base", text)
            self.assertIn("menu", text)


if __name__ == "__main__":
    unittest.main()
