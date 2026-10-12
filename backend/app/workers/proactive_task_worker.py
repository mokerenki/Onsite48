"""Retired compatibility module for the former Redis-stream task worker.

The deployed proactive worker is ``app.services.proactive_worker_entrypoint``.
It claims scheduled executions transactionally and delegates execution to
``scheduled_task_runner``/the authenticated realtime runtime.  This module is
kept only so old imports fail explicitly instead of silently starting a second
LangGraph execution path.
"""

from __future__ import annotations


class ProactiveTaskWorker:
    """Compatibility guard preventing the retired parallel runtime from starting."""

    def __init__(self, *_args, **_kwargs) -> None:
        raise RuntimeError(
            "ProactiveTaskWorker is retired; use "
            "app.services.proactive_worker_entrypoint instead."
        )


__all__ = ["ProactiveTaskWorker"]