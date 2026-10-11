"""Step 13: Observability & Tracing Engine.

Tracks:
- Every pipeline run and individual step (spans)
- Latency per step and overall (in milliseconds)
- Token consumption (prompt/candidate tokens) and dollar costs
- Error rates, failure stack traces, and security events
- Local persistent SQLite storage with optional Langfuse cloud forwarding hook
"""
from __future__ import annotations
import contextlib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

INPUT_TOKEN_COST_PER_K = 0.000075
OUTPUT_TOKEN_COST_PER_K = 0.00030

TELEMETRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry_traces (
    trace_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT,
    duration_ms REAL,
    status TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    llm_calls INTEGER DEFAULT 0,
    cost REAL DEFAULT 0.0,
    tags_json TEXT,
    metadata_json TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS telemetry_spans (
    span_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    parent_span_id TEXT,
    name TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT,
    duration_ms REAL,
    status TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost REAL DEFAULT 0.0,
    metadata_json TEXT,
    error TEXT,
    FOREIGN KEY(trace_id) REFERENCES telemetry_traces(trace_id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Span:
    span_id: str
    trace_id: str
    name: str
    start_time: str
    parent_span_id: Optional[str] = None
    end_time: Optional[str] = None
    duration_ms: float = 0.0
    status: str = "running"
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    metadata: dict = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Trace:
    trace_id: str
    run_id: str
    name: str
    start_time: str
    end_time: Optional[str] = None
    duration_ms: float = 0.0
    status: str = "running"
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    cost: float = 0.0
    tags: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    error: Optional[str] = None
    spans: list[Span] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["spans"] = [s.to_dict() for s in self.spans]
        return d


class Tracer:
    """Telemetry and trace manager backed by SQLite with optional Langfuse forwarder."""

    def __init__(self, db_path: str | Path = "data/crm.db"):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(TELEMETRY_SCHEMA)

        self._active_trace: Optional[Trace] = None
        self._current_span_stack: list[Span] = []
        self._langfuse_client = self._init_langfuse()

    def _init_langfuse(self):
        """Optional Langfuse cloud integration if credentials exist in environment."""
        pub_key = os.getenv("LANGFUSE_PUBLIC_KEY")
        sec_key = os.getenv("LANGFUSE_SECRET_KEY")
        if pub_key and sec_key:
            try:
                from langfuse import Langfuse  # type: ignore
                return Langfuse(public_key=pub_key, secret_key=sec_key)
            except Exception:
                pass
        return None

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def start_trace(
        self,
        run_id: str,
        name: str = "leadflow-pipeline",
        tags: Optional[dict] = None,
        metadata: Optional[dict] = None,
        trace_id: Optional[str] = None,
    ) -> Trace:
        """Start a new trace for a pipeline run."""
        tid = trace_id or f"tr-{uuid.uuid4().hex[:12]}"
        now = _now()
        trace = Trace(
            trace_id=tid,
            run_id=run_id,
            name=name,
            start_time=now,
            tags=tags or {},
            metadata=metadata or {},
        )
        self._active_trace = trace
        self._current_span_stack = []

        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO telemetry_traces (
                    trace_id, run_id, name, start_time, status, tags_json, metadata_json
                ) VALUES (?,?,?,?,?,?,?)""",
                (trace.trace_id, trace.run_id, trace.name, trace.start_time, trace.status,
                 json.dumps(trace.tags), json.dumps(trace.metadata)),
            )

        if self._langfuse_client:
            try:
                self._langfuse_client.trace(id=trace.trace_id, name=trace.name, metadata=trace.metadata, tags=list(trace.tags.keys()))
            except Exception:
                pass

        return trace

    @contextlib.contextmanager
    def span(
        self,
        name: str,
        metadata: Optional[dict] = None,
    ) -> Generator[Span, None, None]:
        """Context manager to measure latency, usage, and status of an individual step."""
        if not self._active_trace:
            self.start_trace(run_id=f"auto-{uuid.uuid4().hex[:8]}")

        assert self._active_trace is not None
        parent_id = self._current_span_stack[-1].span_id if self._current_span_stack else None
        span = Span(
            span_id=f"sp-{uuid.uuid4().hex[:8]}",
            trace_id=self._active_trace.trace_id,
            parent_span_id=parent_id,
            name=name,
            start_time=_now(),
            metadata=metadata or {},
        )
        self._current_span_stack.append(span)
        self._active_trace.spans.append(span)

        t0 = time.perf_counter()
        try:
            yield span
            span.status = "success"
        except Exception as e:
            span.status = "error"
            span.error = str(e)
            raise
        finally:
            t1 = time.perf_counter()
            span.duration_ms = round((t1 - t0) * 1000.0, 2)
            span.end_time = _now()
            span.cost = round(
                (span.input_tokens / 1000.0 * INPUT_TOKEN_COST_PER_K) +
                (span.output_tokens / 1000.0 * OUTPUT_TOKEN_COST_PER_K),
                6
            )

            # Record to trace totals
            if self._active_trace:
                self._active_trace.input_tokens += span.input_tokens
                self._active_trace.output_tokens += span.output_tokens
                if span.input_tokens or span.output_tokens:
                    self._active_trace.llm_calls += 1
                self._active_trace.cost = round(self._active_trace.cost + span.cost, 6)

            # Persist span to database
            with self._get_conn() as conn:
                conn.execute(
                    """INSERT INTO telemetry_spans (
                        span_id, trace_id, parent_span_id, name, start_time, end_time,
                        duration_ms, status, input_tokens, output_tokens, cost, metadata_json, error
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        span.span_id, span.trace_id, span.parent_span_id, span.name,
                        span.start_time, span.end_time, span.duration_ms, span.status,
                        span.input_tokens, span.output_tokens, span.cost,
                        json.dumps(span.metadata), span.error,
                    ),
                )

            if self._langfuse_client:
                try:
                    self._langfuse_client.span(
                        trace_id=span.trace_id,
                        name=span.name,
                        start_time=datetime.fromisoformat(span.start_time),
                        end_time=datetime.fromisoformat(span.end_time) if span.end_time else None,
                        metadata=span.metadata,
                    )
                except Exception:
                    pass

            if self._current_span_stack:
                self._current_span_stack.pop()

    def end_trace(self, status: str = "completed", error: Optional[str] = None) -> Optional[Trace]:
        """Finish the active trace and update summary aggregates."""
        if not self._active_trace:
            return None

        trace = self._active_trace
        trace.status = status
        trace.end_time = _now()
        trace.error = error

        # Calculate duration
        start_dt = datetime.fromisoformat(trace.start_time)
        end_dt = datetime.fromisoformat(trace.end_time)
        trace.duration_ms = round((end_dt - start_dt).total_seconds() * 1000.0, 2)

        with self._get_conn() as conn:
            conn.execute(
                """UPDATE telemetry_traces SET
                    end_time = ?, duration_ms = ?, status = ?,
                    input_tokens = ?, output_tokens = ?, llm_calls = ?,
                    cost = ?, error = ?
                   WHERE trace_id = ?""",
                (
                    trace.end_time, trace.duration_ms, trace.status,
                    trace.input_tokens, trace.output_tokens, trace.llm_calls,
                    trace.cost, trace.error, trace.trace_id,
                ),
            )

        if self._langfuse_client:
            try:
                self._langfuse_client.flush()
            except Exception:
                pass

        self._active_trace = None
        self._current_span_stack = []
        return trace

    def get_trace(self, trace_id: str) -> Trace | None:
        """Load a trace with all child spans from SQLite."""
        with self._get_conn() as conn:
            t_row = conn.execute("SELECT * FROM telemetry_traces WHERE trace_id = ?", (trace_id,)).fetchone()
            if not t_row:
                return None
            s_rows = conn.execute("SELECT * FROM telemetry_spans WHERE trace_id = ? ORDER BY start_time ASC", (trace_id,)).fetchall()

            spans = [
                Span(
                    span_id=r["span_id"],
                    trace_id=r["trace_id"],
                    parent_span_id=r["parent_span_id"],
                    name=r["name"],
                    start_time=r["start_time"],
                    end_time=r["end_time"],
                    duration_ms=r["duration_ms"] or 0.0,
                    status=r["status"],
                    input_tokens=r["input_tokens"] or 0,
                    output_tokens=r["output_tokens"] or 0,
                    cost=r["cost"] or 0.0,
                    metadata=json.loads(r["metadata_json"] or "{}"),
                    error=r["error"],
                )
                for r in s_rows
            ]

            return Trace(
                trace_id=t_row["trace_id"],
                run_id=t_row["run_id"],
                name=t_row["name"],
                start_time=t_row["start_time"],
                end_time=t_row["end_time"],
                duration_ms=t_row["duration_ms"] or 0.0,
                status=t_row["status"],
                input_tokens=t_row["input_tokens"] or 0,
                output_tokens=t_row["output_tokens"] or 0,
                llm_calls=t_row["llm_calls"] or 0,
                cost=t_row["cost"] or 0.0,
                tags=json.loads(t_row["tags_json"] or "{}"),
                metadata=json.loads(t_row["metadata_json"] or "{}"),
                error=t_row["error"],
                spans=spans,
            )


# Default singleton
_default_tracer: Optional[Tracer] = None


def get_tracer(db_path: str = "data/crm.db") -> Tracer:
    global _default_tracer
    if _default_tracer is None or _default_tracer.db_path != str(db_path):
        _default_tracer = Tracer(db_path)
    return _default_tracer
