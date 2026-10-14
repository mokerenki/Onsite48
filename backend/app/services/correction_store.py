"""Qdrant-backed learning from corrections.

When a user corrects, overrides, or approves differently than the agent's
original behavior, the correction is stored as an embedding in Qdrant.
Before the agent calls a similar tool again, suggest_correction() probes the
store for semantic matches, letting the agent learn from feedback without
re-asking the model.
"""
from __future__ import annotations

import hashlib
import json
import os
import structlog
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, VectorParams, Distance, Filter, FieldCondition, MatchValue

from app.core.embeddings import get_embedding_provider

logger = structlog.get_logger("onsite48.services.correction_store")


SIMILARITY_THRESHOLD = 0.85
COLLECTION_NAME = "corrections"


class CorrectionStore:
    """Records user corrections and retrieves similar ones via semantic search."""

    def __init__(self, qdrant_host: str | None = None, qdrant_port: int | None = None):
        self.qdrant_host = qdrant_host or os.getenv("QDRANT_HOST", "qdrant")
        self.qdrant_port = qdrant_port or int(os.getenv("QDRANT_PORT", "6333"))
        self.qdrant_client: Optional[QdrantClient] = None
        self._embedding_fn = None
        self._dim = 0
        self._ready = False

    async def initialize(self) -> None:
        """Connect to Qdrant and ensure collection exists. Safe to call repeatedly."""
        if self._ready:
            return
        try:
            self.qdrant_client = QdrantClient(host=self.qdrant_host, port=self.qdrant_port)
            self._embedding_fn = get_embedding_provider()
            self._dim = await self._embedding_fn.dimension()
            self._ensure_collection()
            self._ready = True
            logger.info("correction_store.initialized", collection=COLLECTION_NAME, dim=self._dim)
        except Exception as exc:
            logger.warning("correction_store.init_failed", error=str(exc)[:200])
            self._ready = False
            self.qdrant_client = None
            self._embedding_fn = None

    @property
    def available(self) -> bool:
        return self._ready and self.qdrant_client is not None and self._embedding_fn is not None

    def _ensure_collection(self) -> None:
        assert self.qdrant_client is not None
        exists = self.qdrant_client.collection_exists(COLLECTION_NAME)
        if not exists:
            self.qdrant_client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=self._dim, distance=Distance.COSINE),
            )
            logger.info("correction_store.collection_created")
        for field_name in ("organization_id", "user_id", "thread_id", "tool_name", "correction_type"):
            try:
                self.qdrant_client.create_payload_index(
                    collection_name=COLLECTION_NAME,
                    field_name=field_name,
                    field_schema="keyword",
                )
            except Exception as exc:
                logger.debug("correction_store.index_failed", field=field_name, error=str(exc)[:120])

    async def record_correction(
        self,
        *,
        organization_id: str,
        user_id: str,
        thread_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
        correction_type: str,
        corrected_args: dict[str, Any] | None = None,
        user_feedback: str | None = None,
        goal: str | None = None,
        context_summary: str | None = None,
    ) -> str:
        """Store a correction as a vector point in Qdrant."""
        if not self.available:
            return ""

        record_id = str(uuid.uuid4())
        text_payload = self._serialize_for_embedding(
            tool_name, tool_args, corrected_args, user_feedback, goal, context_summary
        )
        embedding = await self._embedding_fn.embed(text_payload)

        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "organization_id": organization_id,
            "user_id": user_id,
            "thread_id": thread_id,
            "tool_name": tool_name,
            "correction_type": correction_type,
            "tool_args": tool_args,
            "corrected_args": corrected_args or {},
            "user_feedback": user_feedback or "",
            "goal": goal or "",
            "context_summary": context_summary or "",
            "text_payload": text_payload,
            "created_at": now,
        }

        self.qdrant_client.upsert(
            collection_name=COLLECTION_NAME,
            points=[PointStruct(id=record_id, vector=embedding, payload=payload)],
            wait=True,
        )
        logger.info("correction_store.recorded", id=record_id, tool=tool_name, type=correction_type)
        return record_id

    async def suggest_correction(
        self,
        *,
        organization_id: str,
        user_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
        goal: str | None = None,
        thread_id: str | None = None,
        top_k: int = 5,
        min_score: float = SIMILARITY_THRESHOLD,
    ) -> Optional[dict[str, Any]]:
        """Probe for semantically similar corrections. Returns top match above threshold."""
        if not self.available:
            return None

        query_text = self._serialize_for_embedding(tool_name, tool_args, None, None, goal)
        query_vector = await self._embedding_fn.embed(query_text)

        must_conditions = [
            FieldCondition(key="organization_id", match=MatchValue(value=organization_id)),
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
            FieldCondition(key="tool_name", match=MatchValue(value=tool_name)),
        ]
        if thread_id:
            must_conditions.append(
                FieldCondition(key="thread_id", match=MatchValue(value=thread_id))
            )

        search_result = self.qdrant_client.search(
            collection_name=COLLECTION_NAME,
            query_vector=query_vector,
            query_filter=Filter(must=must_conditions),
            limit=top_k,
            with_payload=True,
            with_vectors=False,
        )

        if not search_result:
            return None

        best = search_result[0]
        score = float(best.score or 0.0)
        if score < min_score:
            logger.debug("correction_store.no_suggestion", tool=tool_name, score=score, threshold=min_score)
            return None

        payload = best.payload or {}
        suggestion = {
            "score": score,
            "correction_type": payload.get("correction_type", "unknown"),
            "corrected_args": payload.get("corrected_args", {}),
            "user_feedback": payload.get("user_feedback", ""),
            "goal": payload.get("goal", ""),
            "context_summary": payload.get("context_summary", ""),
            "record_id": best.id,
            "tool_name": tool_name,
        }
        logger.info("correction_store.suggestion_found", tool=tool_name, score=score)
        return suggestion

    def _serialize_for_embedding(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        corrected_args: dict[str, Any] | None,
        user_feedback: str | None,
        goal: str | None,
        context_summary: str | None,
    ) -> str:
        parts = [f"tool: {tool_name}"]
        if goal:
            parts.append(f"goal: {goal}")
        if tool_args:
            parts.append(f"args: {json.dumps(tool_args, sort_keys=True, default=str)[:2000]}")
        if corrected_args:
            parts.append(f"corrected: {json.dumps(corrected_args, sort_keys=True, default=str)[:2000]}")
        if user_feedback:
            parts.append(f"feedback: {user_feedback}")
        if context_summary:
            parts.append(f"context: {context_summary[:500]}")
        return " ".join(parts)


correction_store = CorrectionStore()
