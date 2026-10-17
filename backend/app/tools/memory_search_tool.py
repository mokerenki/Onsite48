"""Memory search tool — search lessons and facts learned from previously completed tasks.

Wraps the durable memory store (SQLMemoryStore when configured, otherwise the
in-memory store) as a model-callable tool so the agent can recall prior
learnings during a run.
"""
from __future__ import annotations

from typing import Any

from app.tools.registry import BaseTool, ToolSchema
from app.runtime.memory_distiller import get_memory_store


class MemorySearchTool(BaseTool):
    """Search the durable memory store for lessons/facts from past runs."""

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="memory_search",
            description="Search lessons and facts learned from previously completed tasks in this organization.",
            parameters={
                "query": {
                    "type": "string",
                    "description": "Natural-language query describing the lesson or fact to recall.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 5, max 10).",
                    "optional": True,
                },
            },
            required=["query"],
            timeout_seconds=15,
            idempotency="read_only",
            domain="memory",
        )

    async def execute(self, query: str, limit: int = 5) -> dict[str, Any]:
        store = get_memory_store()
        if not hasattr(store, "search"):
            return {"results": [], "note": "memory store unavailable"}
        records = await store.search(query, limit=min(int(limit or 5), 10))
        return {
            "results": [
                {
                    "kind": r.kind,
                    "text": r.text,
                    "source_run_id": r.source_run_id,
                    "confidence": r.confidence,
                }
                for r in records
            ]
        }


def memory_search() -> MemorySearchTool:
    """Factory so the tool can be registered without construction args."""
    return MemorySearchTool()
