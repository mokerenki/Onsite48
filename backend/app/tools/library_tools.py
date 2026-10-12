"""Source Library retrieval tool for the Onsite48 agent runtime."""

from __future__ import annotations

from typing import Any

from app.db import get_session_factory
from app.services.execution_identity import get_execution_identity
from app.services.source_library import search_library
from app.tools.registry import BaseTool, ToolSchema


class LibrarySearchTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="library_search",
            description="Search the current organization's approved Source Library and return cited excerpts. Use it when a task depends on company playbooks, reports, briefs, or reference documents.",
            parameters={"query": {"type": "string"}, "limit": {"type": "integer"}},
            required=["query"],
        )

    async def execute(self, query: str, limit: int = 5, **_: Any) -> dict[str, Any]:
        identity = get_execution_identity()
        async with get_session_factory()() as session:
            results = await search_library(
                session,
                organization_id=identity.organization_id,
                query=query,
                limit=limit,
            )
        return {"query": query, "results": results}