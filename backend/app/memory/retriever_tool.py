# backend/app/memory/retriever_tool.py

from typing import Any, Dict, List, Optional
from datetime import datetime
import structlog

from app.tools.registry import BaseTool, ToolSchema
from app.memory.cognee_setup import CogneeMemory
from app.services.execution_identity import get_execution_identity

logger = structlog.get_logger("aether.memory.retriever_tool")


class MemoryUnavailableError(RuntimeError):
    """Memory is not usable right now, so the task must degrade, not fail."""


def _unavailable_result(reason: str, message: str) -> Dict[str, Any]:
    """A structured 'no memory' answer the agent can keep working from.

    Memory is an optional capability. Returning this -- rather than raising --
    lets the agent answer from the current request instead of replanning against
    a store that will not recover during the task. ``results`` stays an empty
    list so callers that already handle the normal shape keep working.
    """
    return {
        "status": "memory_unavailable",
        "available": False,
        "reason": reason,
        "message": message,
        "results": [],
    }


class MemoryRetrieverTool(BaseTool):
    """Tool callable by the Executor node for memory access with proper tenant/thread scoping."""

    def __init__(self, cognee_memory: CogneeMemory):
        super().__init__()
        self.cognee_memory = cognee_memory
        self._last_search_scope = {}
        logger.info("memory_retriever_tool.initialized")

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="memory_retriever",
            description="Retrieve information from the agent\'s memory using semantic search, graph queries, or temporal queries. Use this tool to get context, facts, or relationships from the agent\'s long-term memory.",
            parameters={
                "mode": {
                    "type": "string", 
                    "enum": ["semantic", "graph", "temporal"], 
                    "description": "The type of memory retrieval to perform: \'semantic\' for vector search, \'graph\' for Cypher queries, or \'temporal\' for time-aware fact retrieval."
                },
                "query": {
                    "type": "string", 
                    "description": "The query string. For \'semantic\' mode, this is natural language. For \'graph\' mode, this is a Cypher query. For \'temporal\' mode, this is the property name to retrieve."
                },
                "entity_label": {
                    "type": "string", 
                    "description": "Required for \'temporal\' mode: The label of the entity (e.g., \'Case\', \'Document\').", 
                    "optional": True
                },
                "entity_id": {
                    "type": "string", 
                    "description": "Required for \'temporal\' mode: The ID of the entity (e.g., \'CASE-2023-001\').", 
                    "optional": True
                },
                "query_time": {
                    "type": "string", 
                    "description": "Required for \'temporal\' mode: The timestamp (ISO 8601 format, e.g., \'2023-10-27T10:00:00Z\') at which to query the property\'s value.", 
                    "optional": True
                },
                "top_k": {
                    "type": "integer", 
                    "description": "Optional: Number of top results for semantic search. Defaults to 5.", 
                    "optional": True
                },
            },
            required=["mode", "query"]
        )

    def _memory_unavailable_reason(self) -> Optional[str]:
        """Why memory cannot answer right now, or ``None`` if it can."""
        if self.cognee_memory is None:
            return "memory_not_configured"
        if not getattr(self.cognee_memory, "memory_available", False):
            return "memory_not_initialized"
        return None

    def _store_is_reachable(self) -> bool:
        """Probe the vector store.

        Used to tell an availability problem apart from a bad query. A
        malformed Cypher statement must still surface as an error, because it is
        the caller's mistake rather than the store's.
        """
        try:
            client = getattr(self.cognee_memory, "qdrant_client", None)
            if client is None:
                return False
            client.collection_exists(
                getattr(self.cognee_memory, "qdrant_collection_name", "aether_memory")
            )
            return True
        except Exception as exc:
            logger.warning("memory_retriever_tool.store_unreachable", error=type(exc).__name__)
            return False

    async def execute(
        self, 
        mode: str, 
        query: str, 
        entity_label: Optional[str] = None, 
        entity_id: Optional[str] = None, 
        query_time: Optional[str] = None, 
        top_k: int = 5
    ) -> Dict[str, Any]:
        """Executes a memory retrieval operation with proper tenant/thread scoping."""
        logger.info("memory_retriever_tool.execute", 
                    mode=mode, 
                    query=query[:100], 
                    entity_id=entity_id, 
                    query_time=query_time)

        unavailable = self._memory_unavailable_reason()
        if unavailable is not None:
            logger.warning("memory_retriever_tool.degraded", reason=unavailable, mode=mode)
            return _unavailable_result(
                unavailable,
                "Long-term memory is unavailable right now. Answer from the current "
                "request and do not claim to recall earlier context.",
            )

        try:
            # ─── SCOPE ENFORCEMENT ──────────────────────────────────────
            # Always enforce tenant/thread isolation
            identity = get_execution_identity()
            scope = {
                "organization_id": str(identity.organization_id),
                "user_id": str(identity.user_id),
                "thread_id": identity.task_thread_id,
            }
            
            self._last_search_scope = scope
            logger.debug("memory_retriever_tool.scope_enforced", scope=scope)

            if mode == "semantic":
                return await self.cognee_memory.search(
                    query, 
                    mode="semantic", 
                    top_k=top_k, 
                    **scope
                )
            elif mode == "graph":
                return await self.cognee_memory.search(
                    query, 
                    mode="graph", 
                    top_k=top_k, 
                    **scope
                )
            elif mode == "temporal":
                if not entity_label or not entity_id or not query_time:
                    raise ValueError("entity_label, entity_id, and query_time are required for temporal mode.")
                try:
                    parsed_query_time = datetime.fromisoformat(query_time.replace("Z", "+00:00"))
                except ValueError:
                    raise ValueError("Invalid query_time format. Must be ISO 8601 string (e.g., 2023-10-27T10:00:00Z).")
                
                return await self.cognee_memory.search(
                    query=query,
                    mode="temporal", 
                    entity_label=entity_label,
                    entity_id=entity_id,
                    property_name=query,
                    query_time=parsed_query_time,
                    **scope,
                )
            else:
                raise ValueError(f"Unsupported memory retrieval mode: {mode}")
        except MemoryUnavailableError:
            raise
        except Exception as e:
            if not self._store_is_reachable():
                # The store is down, not the query. Degrading here is what keeps
                # an optional capability from failing the whole task.
                logger.warning(
                    "memory_retriever_tool.degraded",
                    reason="vector_store_unreachable",
                    mode=mode,
                    error=type(e).__name__,
                )
                return _unavailable_result(
                    "vector_store_unreachable",
                    "Long-term memory is temporarily unreachable. Answer from the "
                    "current request and do not claim to recall earlier context.",
                )
            logger.error("memory_retriever_tool.execution_failed", 
                         mode=mode, 
                         query=query[:100], 
                         error=str(e), 
                         exc_info=True)
            raise