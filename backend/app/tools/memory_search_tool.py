# backend/app/tools/memory_search_tool.py

from __future__ import annotations

from typing import Any

import structlog

from app.runtime.memory_distiller import get_memory_store
from app.services.execution_identity import get_execution_identity
from app.tools.registry import BaseTool, ToolSchema

logger = structlog.get_logger("aether.tools.memory_search")


class MemorySearchTool(BaseTool):
    """Search durable memory records stored by the memory distiller.

    Uses the configured SQLMemoryStore (falls back to InMemoryMemoryStore)
    to find previously distilled facts, procedures, preferences, and open
    questions relevant to a query string.
    """

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="memory_search",
            description=(
                "Search the agent's long-term memory for previously distilled "
                "facts, procedures, preferences, and open questions from past "
                "task runs. Use this when you need to recall context from "
                "earlier work before planning new actions."
            ),
            parameters={
                "query": {
                    "type": "string",
                    "description": "Natural language query to match against stored memory records.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of results to return. Defaults to 5.",
                    "optional": True,
                },
                "org_id": {
                    "type": "string",
                    "description": "Organization ID to scope the search. Defaults to 'default'.",
                    "optional": True,
                },
            },
            required=["query"],
            resources=["memory_record"],
            operations=["search"],
            exactness="estimated",
            idempotency="read_only",
            timeout_seconds=30,
            max_attempts=2,
        )

    async def execute(
        self,
        query: str,
        top_k: int = 5,
        org_id: str = "default",
    ) -> dict[str, Any]:
        # Cap page size so callers cannot request unbounded result sets.
        top_k = max(1, min(int(top_k), 20))

        # Scope the search to the caller's organization from the execution
        # identity context, which is set by the runtime loop for authenticated
        # task execution. Ignore caller-supplied org_id if it does not match.
        try:
            identity = get_execution_identity()
            org_id = str(identity.organization_id)
        except RuntimeError:
            # No active execution context; use the caller-provided org_id as-is.
            pass

        store = get_memory_store()
        try:
            records = await store.search(query, limit=top_k, org_id=org_id)
        except Exception as exc:
            logger.warning("memory_search.store_error", error=str(exc)[:200])
            return {
                "status": "memory_unavailable",
                "available": False,
                "reason": str(exc)[:200],
                "message": "Memory search is temporarily unavailable. Answer from the current request.",
                "results": [],
            }

        results = [
            {
                "id": r.id,
                "kind": r.kind,
                "text": r.text,
                "source_run_id": r.source_run_id,
                "evidence_urls": r.evidence_urls,
                "tags": r.tags,
                "confidence": r.confidence,
                "created_at": r.created_at,
            }
            for r in records
        ]

        return {
            "status": "ok",
            "available": True,
            "query": query,
            "count": len(results),
            "results": results,
        }


def memory_search() -> MemorySearchTool:
    """Factory function matching the registration pattern in main.py lifespan."""
    return MemorySearchTool()
