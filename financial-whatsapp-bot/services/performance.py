"""Opt-in baseline measurements. No message content, identifiers or credentials.

Observers never change queries, responses or retry policies. Supabase counts are
execute() calls, not SQL statements or HTTP retries internal to the SDK.
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path


logger = logging.getLogger("financial.performance")
PREFIX = "FINANCIAL_PERF "
_current: ContextVar[Trace | None] = ContextVar("financial_performance", default=None)
_file_lock = threading.Lock()
_INPUTS = {"text", "interactive", "button", "audio"}
CASES = {
    "other", "onboarding", "expert", "diagnostic", "funds", "finances",
    "calendar", "roadmap", "reminders", "menu", "panel", "tax_info",
    "financial_parse",
}


def enabled() -> bool:
    return os.getenv("PERFORMANCE_METRICS_ENABLED", "false").strip().lower() == "true"


def _label(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", value)[:64] or "baseline"


@dataclass
class Trace:
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    run: str = field(default_factory=lambda: _label(os.getenv("PERFORMANCE_RUN_LABEL", "baseline")))
    case: str = "other"
    input_type: str = "text"
    phase: str = "web"
    attempt: int = 1
    started_at: float = field(default_factory=time.time)
    local_start: float = field(default_factory=time.perf_counter)
    queue_wait_ms: float | None = None
    stages: dict = field(default_factory=dict)
    db_operations: dict = field(default_factory=dict)
    accepted_sends: int = 0
    first_response_ms: float | None = None
    last_response_ms: float | None = None
    outcome: str = "completed"
    excluded: bool = False
    handed_off: bool = False
    clock_warning: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, stage: str, elapsed_ms: float, failed: bool = False) -> None:
        with self._lock:
            row = self.stages.setdefault(stage, {"calls": 0, "ms": 0.0, "errors": 0})
            row["calls"] += 1
            row["ms"] += elapsed_ms
            row["errors"] += int(failed)

    def accepted(self) -> None:
        if self.phase == "web":
            elapsed = (time.perf_counter() - self.local_start) * 1000
        else:
            # Across processes only wall-clock timestamps can be compared.
            elapsed = (time.time() - self.started_at) * 1000
        with self._lock:
            self.accepted_sends += 1
            if elapsed < 0:
                self.clock_warning = True
                return
            if self.first_response_ms is None:
                self.first_response_ms = elapsed
            self.last_response_ms = elapsed

    def record(self) -> dict:
        with self._lock:
            return {
                "schema": 1,
                "trace_id": self.trace_id,
                "run": self.run,
                "case": self.case,
                "input_type": self.input_type,
                "phase": self.phase,
                "attempt": self.attempt,
                "started_at": datetime.fromtimestamp(self.started_at, timezone.utc).isoformat(),
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_ms": round((time.perf_counter() - self.local_start) * 1000, 3),
                "queue_wait_ms": self.queue_wait_ms,
                "first_response_ms": self.first_response_ms,
                "last_response_ms": self.last_response_ms,
                "accepted_sends": self.accepted_sends,
                "outcome": self.outcome,
                "clock_warning": self.clock_warning,
                "stages": {key: {**row, "ms": round(row["ms"], 3)} for key, row in self.stages.items()},
                "db_operations": dict(self.db_operations),
            }


def _emit(trace: Trace) -> None:
    if trace.excluded:
        return
    # Instrumentation failures must not prevent a response or cause a retry.
    try:
        line = json.dumps(trace.record(), ensure_ascii=True, separators=(",", ":"))
        logger.info("%s%s", PREFIX, line)
        directory = os.getenv("PERFORMANCE_OUTPUT_DIR", "").strip()
        if directory:
            with _file_lock:
                path = Path(directory)
                path.mkdir(parents=True, exist_ok=True)
                # Web and worker use separate files: no cross-process file lock.
                with (path / f"metrics-{os.getpid()}.jsonl").open("a", encoding="utf-8") as output:
                    output.write(line + "\n")
    except Exception:
        logger.warning("No fue posible escribir una medicion de rendimiento")


@contextmanager
def trace_scope(trace: Trace):
    token = _current.set(trace)
    try:
        yield trace
    except BaseException:
        trace.outcome = "failed"
        raise
    finally:
        if trace.outcome != "failed":
            if trace.handed_off:
                trace.outcome = "queued"
            elif not trace.accepted_sends:
                trace.outcome = "no_reply"
        _current.reset(token)
        _emit(trace)


@contextmanager
def span(name: str):
    trace = _current.get()
    if trace is None or trace.excluded:
        yield
        return
    start = time.perf_counter()
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        trace.add(name, (time.perf_counter() - start) * 1000, failed)


def measure(name: str):
    def decorate(function):
        if inspect.iscoroutinefunction(function):
            @wraps(function)
            async def async_wrapper(*args, **kwargs):
                with span(name):
                    return await function(*args, **kwargs)
            return async_wrapper

        @wraps(function)
        def sync_wrapper(*args, **kwargs):
            with span(name):
                return function(*args, **kwargs)
        return sync_wrapper
    return decorate


def set_case(case: str) -> None:
    trace = _current.get()
    if trace is not None and case in CASES:
        trace.case = case


def observe_route(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        with span("routing"):
            result = function(*args, **kwargs)
        trace = _current.get()
        if trace is not None and isinstance(result, str):
            if result.startswith("__AI_QUERY"):
                # The advisor/RAG is deliberately absent from this baseline.
                trace.excluded = True
            elif result == "__FINANCIAL_PARSE__":
                trace.case = "financial_parse"
        return result
    return wrapper


def observe_webhook(function):
    @wraps(function)
    async def wrapper(request, *args, **kwargs):
        if not enabled():
            return await function(request, *args, **kwargs)
        start, epoch = time.perf_counter(), time.time()
        try:
            payload = json.loads(await request.body())
            incoming = [
                message
                for entry in payload.get("entry", [])
                for change in entry.get("changes", [])
                for message in change.get("value", {}).get("messages", [])
            ]
        except (ValueError, TypeError, AttributeError):
            return await function(request, *args, **kwargs)
        # Do not attribute a batch's total time to one message. Status-only
        # callbacks, duplicates and batches are not ordinary response samples.
        if len(incoming) != 1 or not isinstance(incoming[0], dict):
            return await function(request, *args, **kwargs)
        input_type = incoming[0].get("type")
        trace = Trace(
            input_type=input_type if input_type in _INPUTS else "other",
            started_at=epoch, local_start=start,
        )
        with trace_scope(trace):
            return await function(request, *args, **kwargs)
    return wrapper


def financial_queue_kwargs() -> dict:
    trace = _current.get()
    if trace is None or trace.excluded:
        return {}
    trace.handed_off = True
    return {"performance_context": {
        "trace_id": trace.trace_id, "run": trace.run,
        "started_at": trace.started_at, "enqueued_at": time.time(),
        "input_type": trace.input_type,
    }}


def observe_financial_job(function):
    @wraps(function)
    async def wrapper(ctx, *args, **kwargs):
        context = kwargs.get("performance_context")
        if not enabled() or not context:
            return await function(ctx, *args, **kwargs)
        try:
            trace_id = str(context["trace_id"])
            if not re.fullmatch(r"[a-f0-9]{32}", trace_id):
                raise ValueError("Invalid trace")
            started_at = float(context["started_at"])
            queue_wait = (time.time() - float(context["enqueued_at"])) * 1000
            trace = Trace(
                trace_id=trace_id, run=_label(str(context["run"])),
                case="financial_parse", phase="worker", started_at=started_at,
                input_type=context.get("input_type") if context.get("input_type") in _INPUTS else "other",
                attempt=max(1, int(ctx.get("job_try", 1))),
                queue_wait_ms=round(queue_wait, 3) if queue_wait >= 0 else None,
                clock_warning=queue_wait < 0,
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            # Bad measurement metadata must never fail a business job.
            return await function(ctx, *args, **kwargs)
        with trace_scope(trace):
            return await function(ctx, *args, **kwargs)
    return wrapper


def observe_meta_send(function):
    @wraps(function)
    async def wrapper(*args, **kwargs):
        with span("meta_send"):
            result = await function(*args, **kwargs)
        trace = _current.get()
        if trace is not None and isinstance(result, dict) and result.get("messages"):
            trace.accepted()
        return result
    return wrapper


class _ObservedQuery:
    def __init__(self, query, operation: str):
        self._query = query
        self._operation = operation

    def __getattr__(self, name):
        value = getattr(self._query, name)
        if not callable(value):
            return value

        @wraps(value)
        def chained(*args, **kwargs):
            result = value(*args, **kwargs)
            return _ObservedQuery(result, self._operation) if hasattr(result, "execute") else result
        return chained

    def execute(self, *args, **kwargs):
        trace = _current.get()
        if trace is not None and not trace.excluded:
            with trace._lock:
                trace.db_operations[self._operation] = trace.db_operations.get(self._operation, 0) + 1
        with span("supabase"), span("supabase." + self._operation):
            return self._query.execute(*args, **kwargs)


class _ObservedSupabase:
    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        return getattr(self._client, name)

    def table(self, name, *args, **kwargs):
        return _ObservedQuery(self._client.table(name, *args, **kwargs), "table:" + _label(str(name)))

    def from_(self, name, *args, **kwargs):
        return _ObservedQuery(self._client.from_(name, *args, **kwargs), "table:" + _label(str(name)))

    def rpc(self, name, *args, **kwargs):
        return _ObservedQuery(self._client.rpc(name, *args, **kwargs), "rpc:" + _label(str(name)))


def instrument_supabase(client):
    if client is None or not enabled() or isinstance(client, _ObservedSupabase):
        return client
    return _ObservedSupabase(client)
