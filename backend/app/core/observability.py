"""Observability foundation: structured logs + Prometheus + OpenTelemetry/Jaeger.

Called once from ``main.py`` lifespan so every process (API, worker, proactive
worker) gets the same configuration surface from environment variables.
"""
from __future__ import annotations

import os
from typing import Any

import structlog

from app.core.platform_metrics import metrics


def setup_logging() -> None:
    """Configure structlog for JSON structured output with trace context."""
    processors: list[Any] = [
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ]
    try:
        from opentelemetry import trace as otel_trace  # type: ignore[import-untyped]

        class TraceContextProcessor:
            """Inject current trace_id and span_id into every log record."""

            def __call__(self, logger: Any, method_name: str, event: dict[str, Any]) -> dict[str, Any]:
                try:
                    span = otel_trace.get_current_span()
                    if span and span.is_recording():
                        ctx = span.get_span_context()
                        if ctx.trace_id and ctx.span_id:
                            event["trace_id"] = format(ctx.trace_id, "032x")
                            event["span_id"] = format(ctx.span_id, "016x")
                except Exception:
                    pass
                if "trace_id" not in event:
                    from app.core.tracing import current_trace_id

                    tid = current_trace_id()
                    if tid:
                        event["trace_id"] = tid
                return event

        processors.insert(2, TraceContextProcessor())
    except Exception:
        pass

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.WriteLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def setup_telemetry() -> bool:
    """Wire up OpenTelemetry with a Jaeger exporter and instrument libraries.

    Returns True if tracing is enabled, False if it was skipped.
    """
    if os.getenv("OTEL_TRACES_EXPORTER", "jaeger").lower() not in ("jaeger", "otlp", "console", "all"):
        return False

    try:
        from opentelemetry import trace as otel_trace  # type: ignore[import-untyped]
        from opentelemetry.exporter.jaeger.thrift import (  # type: ignore[import-untyped]
            JaegerExporter,
        )
        from opentelemetry.instrumentation.asyncpg import (  # type: ignore[import-untyped]
            AsyncPGInstrumentor,
        )
        from opentelemetry.instrumentation.fastapi import (  # type: ignore[import-untyped]
            FastAPIInstrumentor,
        )
        from opentelemetry.instrumentation.redis import (  # type: ignore[import-untyped]
            RedisInstrumentor,
        )
        from opentelemetry.instrumentation.sqlalchemy import (  # type: ignore[import-untyped]
            SQLAlchemyInstrumentor,
        )
        from opentelemetry.sdk.resources import Resource  # type: ignore[import-untyped]
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-untyped]
        from opentelemetry.sdk.trace.export import (  # type: ignore[import-untyped]
            BatchSpanProcessor,
        )
    except ImportError:
        return False

    service_name = os.getenv("OTEL_SERVICE_NAME", "onsite48-api")
    resource = Resource.create(
        {"service.name": service_name, "service.instance.id": os.environ.get("HOSTNAME", "")}
    )
    provider = TracerProvider(resource=resource)

    jaeger_endpoint = (
        os.getenv("JAEGER_ENDPOINT")
        or os.getenv("OTEL_EXPORTER_JAEGER_ENDPOINT")
        or "http://jaeger:14268/api/traces"
    )

    jaeger_exporter = JaegerExporter(
        agent_host_name=os.getenv("JAEGER_HOST", "jaeger"),
        agent_port=int(os.getenv("JAEGER_PORT", "6831")),
        collector_endpoint=jaeger_endpoint,
    )
    provider.add_span_processor(BatchSpanProcessor(jaeger_exporter))
    otel_trace.set_tracer_provider(provider)

    FastAPIInstrumentor().instrument()
    try:
        RedisInstrumentor().instrument()
    except Exception:
        pass
    try:
        AsyncPGInstrumentor().instrument()
    except Exception:
        pass
    try:
        SQLAlchemyInstrumentor().instrument()
    except Exception:
        pass

    return True


def record_observation(
    name: str,
    value: float,
    labels: dict[str, Any] | None = None,
    kind: str = "histogram",
) -> None:
    """Record a custom observation using the in-process platform metrics registry."""
    if kind == "counter":
        metrics.inc(name, value, labels)
    elif kind == "gauge":
        metrics.set(name, value, labels)
    elif kind == "histogram":
        metrics.observe(name, value, labels)


# Pre-registered metric names for discoverability and consistent naming.
METRIC_NAMES = {
    "task_duration": "onsite48_task_duration_seconds",
    "task_queue_depth": "onsite48_task_queue_depth",
    "model_latency": "onsite48_model_latency_seconds",
    "ws_connections": "onsite48_ws_connections",
    "provider_health": "onsite48_provider_health",
    "ws_subscriptions": "onsite48_ws_subscriptions",
    "ws_broadcast_local": "onsite48_ws_broadcast_local",
    "ws_broadcast_redis": "onsite48_ws_broadcast_redis",
    "queue_claims": "onsite48_queue_claims_total",
    "queue_retries": "onsite48_queue_retries_total",
    "queue_dead_letter": "onsite48_queue_dead_letter_total",
}
