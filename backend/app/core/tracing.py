"""Lightweight, always-available execution tracing for the agent runtime.

The runtime is a multi-step agent: a user goal becomes a plan, the plan becomes
tool calls, tool results are verified, and failures trigger replans. When a task
fails at step 7 of 12 there is currently no way to answer "what did the model
actually see, which provider answered, what did that tool return, and which
validator rejected the plan" without adding print statements and guessing.

This module provides that answer with three properties:

1. It is cheap and always on in memory, so spans exist even when no exporter is
   configured. Tracing must never be the reason a task fails.
2. It is exporter agnostic. Spans land in a bounded in-memory ring that the task
   state can carry (for API responses and debugging), optionally in a JSONL file
   for offline analysis, and optionally in Opik when explicitly enabled.
3. It is redaction aware. Tool arguments and model output frequently contain
   email addresses, message bodies, and connector payloads, so span attributes
   are truncated and secret-looking keys are masked before they are recorded.

A span is a context manager and nests through a ContextVar, so instrumenting a
call site is a two-line change and produces a proper parent/child tree even
across concurrent step batches.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

import structlog

logger = structlog.get_logger("onsite48.core.tracing")

# Bounded in-memory ring. Enough to reconstruct a single task trajectory without
# unbounded growth in long-lived workers.
MAX_REMEMBERED_SPANS = int(os.getenv("SYNTHAI_TRACE_MEMORY_SPANS", "2000"))
MAX_ATTRIBUTE_CHARS = int(os.getenv("SYNTHAI_TRACE_ATTR_CHARS", "600"))
MAX_PREVIEW_CHARS = int(os.getenv("SYNTHAI_TRACE_PREVIEW_CHARS", "2000"))

_SECRET_KEY_HINTS = (
    "api_key", "apikey", "authorization", "auth_token", "access_token",
    "refresh_token", "client_secret", "password", "passwd", "secret",
    "session_token", "cookie", "bearer", "private_key",
)
_MASK = "[redacted]"


def _truthy(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _looks_secret(key: str) -> bool:
    lowered = str(key).lower()
    return any(hint in lowered for hint in _SECRET_KEY_HINTS)


def redact_value(key: str, value: Any) -> Any:
    """Mask secret-looking values and bound the size of everything else."""
    if _looks_secret(key):
        return _MASK
    if isinstance(value, str) and len(value) > MAX_ATTRIBUTE_CHARS:
        return value[:MAX_ATTRIBUTE_CHARS] + f"...<truncated {len(value) - MAX_ATTRIBUTE_CHARS} chars>"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [redact_value(key, item) for item in list(value)[:20]]
    if isinstance(value, dict):
        return {str(k): redact_value(str(k), v) for k, v in list(value.items())[:20]}
    text = str(value)
    if len(text) > MAX_ATTRIBUTE_CHARS:
        return text[:MAX_ATTRIBUTE_CHARS] + f"...<truncated {len(text) - MAX_ATTRIBUTE_CHARS} chars>"
    return text


def redact_mapping(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {str(k): redact_value(str(k), v) for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [redact_mapping(item) for item in list(payload)[:20]]
    return redact_value("value", payload)


@dataclass
class Span:
    """One timed unit of agent work."""

    name: str
    kind: str = "step"
    trace_id: str = ""
    span_id: str = ""
    parent_id: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    status: str = "ok"
    error: Optional[str] = None

    @property
    def duration_ms(self) -> float:
        end = self.end_time if self.end_time is not None else time.time()
        return round((end - self.start_time) * 1000, 2)

    def set(self, key: str, value: Any) -> "Span":
        self.attributes[str(key)] = redact_value(key, value)
        return self

    def update(self, values: Dict[str, Any]) -> "Span":
        for key, value in (values or {}).items():
            self.set(key, value)
        return self

    def as_dict(self) -> Dict[str, Any]:
        record: Dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
        }
        if self.parent_id:
            record["parent_id"] = self.parent_id
        if self.error:
            record["error"] = self.error
        return record


class _MemorySink:
    """Bounded ring of the most recent spans, safe for concurrent use."""

    def __init__(self, capacity: int) -> None:
        self.capacity = max(1, capacity)
        self._records: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def write(self, record: Dict[str, Any]) -> None:
        with self._lock:
            self._records.append(record)
            if len(self._records) > self.capacity:
                del self._records[: len(self._records) - self.capacity]

    def tail(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._records[-max(1, int(limit)):])

    def by_trace(self, trace_id: str, limit: int = 500) -> List[Dict[str, Any]]:
        return [record for record in self.tail(limit) if record.get("trace_id") == trace_id][:limit]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


_MEMORY_SINK = _MemorySink(MAX_REMEMBERED_SPANS)

_CURRENT_TRACE: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("onsite48_trace_id", default=None)
_CURRENT_SPAN: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("onsite48_span_id", default=None)

_opik_module: Any = None
_opik_checked = False
_file_lock = threading.Lock()


def _opik() -> Any:
    """Import Opik lazily so tracing never pays for it unless it is enabled."""
    global _opik_module, _opik_checked
    if _opik_checked:
        return _opik_module
    _opik_checked = True
    if not _truthy(os.getenv("SYNTHAI_TRACE_OPIK"), default=False):
        return None
    try:
        import opik  # type: ignore

        _opik_module = opik
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("tracing.opik_unavailable", error_type=type(exc).__name__)
        _opik_module = None
    return _opik_module


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


def set_trace(trace_id: Optional[str] = None) -> str:
    """Start (or join) a trace for the current context."""
    resolved = trace_id or new_trace_id()
    _CURRENT_TRACE.set(resolved)
    return resolved


def current_trace_id() -> str:
    return _CURRENT_TRACE.get() or ""


#: The header a caller uses to carry a trace across services, and the one the
#: API echoes so a user-visible identifier and the server log agree.
TRACE_HEADER = "X-Trace-Id"


def _sanitize_trace_id(candidate: Optional[str]) -> str:
    """Accept a caller-supplied trace id only if it is a plain hex token.

    The value is echoed into a response header and written into task event
    payloads. An unbounded, unvalidated string from a client is a log-injection
    and header-injection vector and a way to write megabytes into the event
    table, so anything that is not 8-64 hex characters is replaced rather than
    trusted.
    """
    if not candidate:
        return ""
    trimmed = candidate.strip()
    if not (8 <= len(trimmed) <= 64):
        return ""
    if any(character not in "0123456789abcdefABCDEF" for character in trimmed):
        return ""
    return trimmed


def bind_request_trace(headers: Any) -> str:
    """Establish the trace for one request or WebSocket connection.

    Previously nothing set a trace at the boundary. `span()` minted one lazily
    when it found no current trace, so a single task that produced several spans
    could end up with a different trace id per span -- the trace could not be
    reassembled, and there was no id to give a user who is reporting a problem.

    Joins a caller's trace when the header carries a well-formed one, so a
    frontend, a proxy and the backend end up in the same trace.
    """
    incoming = ""
    try:
        incoming = headers.get(TRACE_HEADER, "") or ""
    except Exception:  # pragma: no cover - a header map that raises is not fatal
        incoming = ""
    return set_trace(_sanitize_trace_id(incoming) or None)


def _jsonl_path() -> Optional[str]:
    path = os.getenv("SYNTHAI_TRACE_FILE")
    if not path:
        return None
    return path.strip() or None


def _write_jsonl(record: Dict[str, Any]) -> None:
    path = _jsonl_path()
    if not path:
        return
    try:
        with _file_lock, open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
    except Exception as exc:  # pragma: no cover - disk issues must not fail a task
        logger.warning("tracing.jsonl_write_failed", error_type=type(exc).__name__)


def _mirror_to_opik(record: Dict[str, Any]) -> None:
    opik = _opik()
    if opik is None:
        return
    try:
        payload = {
            "input": {"span": record.get("name")},
            "output": {
                "status": record.get("status"),
                "duration_ms": record.get("duration_ms"),
                "attributes": record.get("attributes"),
            },
            "metadata": {
                "trace_id": record.get("trace_id"),
                "span_id": record.get("span_id"),
                "kind": record.get("kind"),
            },
        }
        if record.get("error"):
            payload["error"] = record["error"]
        opik.log(payload)
    except Exception as exc:  # pragma: no cover - exporter must never fail a task
        logger.warning("tracing.opik_mirror_failed", error_type=type(exc).__name__)


def emit_span(span: Span) -> Dict[str, Any]:
    """Persist a finished span to every configured sink."""
    record = span.as_dict()
    _MEMORY_SINK.write(record)
    _write_jsonl(record)
    _mirror_to_opik(record)
    return record


@contextlib.contextmanager
def span(name: str, kind: str = "step", **attributes: Any) -> Iterator[Span]:
    """Time a unit of work and attach it to the current trace.

    Instrumentation is intentionally exception-transparent: a failure inside the
    block is recorded on the span and re-raised unchanged.
    """
    current = Span(
        name=str(name),
        kind=str(kind),
        trace_id=current_trace_id() or set_trace(),
        span_id=uuid.uuid4().hex[:16],
        parent_id=_CURRENT_SPAN.get(),
    )
    current.update(attributes)
    span_token = _CURRENT_SPAN.set(current.span_id)
    try:
        yield current
    except BaseException as exc:
        current.status = "error"
        current.error = f"{type(exc).__name__}: {exc}"[:500]
        raise
    finally:
        current.end_time = time.time()
        _CURRENT_SPAN.reset(span_token)
        emit_span(current)


def record_event(name: str, kind: str = "event", **attributes: Any) -> Dict[str, Any]:
    """Record an instantaneous point-in-time event inside the current trace."""
    current = Span(
        name=str(name),
        kind=str(kind),
        trace_id=current_trace_id() or set_trace(),
        span_id=uuid.uuid4().hex[:16],
        parent_id=_CURRENT_SPAN.get(),
    )
    current.update(attributes)
    current.end_time = current.start_time
    return emit_span(current)


def recent_spans(limit: int = 200) -> List[Dict[str, Any]]:
    return _MEMORY_SINK.tail(limit)


def trace_spans(limit: int = 500) -> List[Dict[str, Any]]:
    trace_id = current_trace_id()
    if not trace_id:
        return []
    return _MEMORY_SINK.by_trace(trace_id, limit)


def trace_summary(limit: int = 500) -> Dict[str, Any]:
    """A compact, serializable view of the current trace for task state."""
    spans = trace_spans(limit)
    durations = [span.get("duration_ms", 0.0) for span in spans]
    errors = [span for span in spans if span.get("status") == "error"]
    model_calls = [span for span in spans if span.get("kind") == "model_call"]
    return {
        "trace_id": current_trace_id(),
        "span_count": len(spans),
        "error_count": len(errors),
        "total_duration_ms": round(sum(durations), 2),
        "slowest_span": max(spans, key=lambda s: s.get("duration_ms", 0.0))["name"] if spans else None,
        "model_call_count": len(model_calls),
        "model_tokens": sum(
            (span.get("attributes", {}).get("total_tokens") or 0) for span in model_calls
        ),
        "model_cost_usd": round(
            sum((span.get("attributes", {}).get("cost_usd") or 0.0) for span in model_calls), 6
        ),
        "errors": [
            {"name": span.get("name"), "error": span.get("error")} for span in errors[:10]
        ],
    }


def reset() -> None:
    """Clear remembered spans. Test and worker-isolation helper."""
    _MEMORY_SINK.clear()