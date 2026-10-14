"""Embedding provider abstraction.

Provides a minimal async embedding interface with optional Redis caching,
matching the pattern used by CogneeMemory but usable standalone for the
correction store and other services that need embeddings without the full
memory initialization stack.
"""
from __future__ import annotations

import hashlib
import json
import os
import structlog
from typing import Optional
import redis.asyncio as aioredis

logger = structlog.get_logger("onsite48.core.embeddings")


class EmbeddingProvider:
    """Async embedding provider with Redis-backed caching."""

    def __init__(self):
        self._client = None
        self._redis: Optional[aioredis.Redis] = None
        self._model = os.getenv("OPENAI_EMBEDDING_MODEL", "nvidia/nv-embed-v1")
        self._base_url = os.getenv("OPENAI_API_BASE")
        self._api_key = os.getenv("OPENAI_API_KEY")
        self._dim = 1024
        self._redis_url = os.getenv("REDIS_URL", os.getenv("redis_url", "redis://redis:6379"))

    @property
    def dimension(self) -> int:
        return self._dim

    async def dim(self) -> int:
        return self._dim

    async def _ensure_client(self):
        if self._client is not None:
            return
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)

        # Determine dimension from a test call
        try:
            resp = await self._client.embeddings.create(input="test", model=self._model)
            self._dim = len(resp.data[0].embedding)
            logger.info("embeddings.dim_resolved", dim=self._dim)
        except Exception as exc:
            logger.warning("embeddings.dim_failed", error=str(exc)[:160], fallback=self._dim)

        # Redis cache
        if not self._redis:
            try:
                self._redis = aioredis.from_url(self._redis_url)
                await self._redis.ping()
                logger.info("embeddings.cache_redis_connected")
            except Exception as exc:
                logger.warning("embeddings.cache_redis_unavailable", error=str(exc)[:160])

    def _cache_key(self, text: str) -> str:
        payload = f"{self._model}:{text}"
        return f"onsite48:embed:{hashlib.sha256(payload.encode()).hexdigest()}"

    async def embed(self, text: str) -> list[float]:
        """Generate or fetch a cached embedding for `text`."""
        await self._ensure_client()
        if not self._client:
            raise RuntimeError("Embedding provider not initialized")

        # Check cache
        if self._redis:
            cached = await self._redis.get(self._cache_key(text))
            if cached:
                return json.loads(cached)

        resp = await self._client.embeddings.create(input=text, model=self._model)
        embedding = resp.data[0].embedding

        # Cache
        if self._redis:
            await self._redis.setex(self._cache_key(text), 7 * 24 * 3600, json.dumps(embedding))

        return embedding


_provider: Optional[EmbeddingProvider] = None


def get_embedding_provider() -> EmbeddingProvider:
    """Get the singleton embedding provider."""
    global _provider
    if _provider is None:
        _provider = EmbeddingProvider()
    return _provider
