"""Canonical graph-runtime assembly shared by interactive and worker paths."""

from __future__ import annotations

from typing import Any

from app.graph import create_graph


def build_task_graph(runtime: Any, *, checkpointer: Any | None = None):
    """Build the same graph from either Onsite48Context or a runtime mapping."""
    if isinstance(runtime, dict):
        model_router = runtime["model_router"]
        domain_router = runtime["domain_router"]
        registry = runtime["tool_registry"]
        active_checkpointer = checkpointer if checkpointer is not None else runtime.get("checkpointer")
    else:
        runtime.ensure_initialized()
        model_router = runtime.model_router
        domain_router = runtime.domain_router
        registry = runtime.tool_registry
        active_checkpointer = checkpointer if checkpointer is not None else runtime.checkpointer
    return create_graph(model_router, domain_router, registry, active_checkpointer)


def runtime_dependencies(runtime: Any) -> dict[str, Any]:
    """Expose the canonical dependency bundle for APIs, workers, and resumes."""
    if isinstance(runtime, dict):
        return {key: runtime.get(key) for key in ("model_router", "domain_router", "tool_registry", "checkpointer", "browser_service", "mcp_registry")}
    runtime.ensure_initialized()
    return {key: getattr(runtime, key, None) for key in ("model_router", "domain_router", "tool_registry", "checkpointer", "browser_service", "mcp_registry")}