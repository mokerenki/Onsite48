import json
import structlog
from datetime import datetime, timezone
from typing import Any, Dict, Optional, AsyncIterator, Sequence, Tuple
import redis.asyncio as redis

# LangGraph checkpoint types
try:
    from langgraph.checkpoint.base import (
        BaseCheckpointSaver,
        Checkpoint,
        CheckpointMetadata,
        CheckpointTuple,
        ChannelVersions,
        RunnableConfig,
        SerializerProtocol,
    )
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
except ImportError:
    # Fallback for older langgraph versions — define minimal compat
    BaseCheckpointSaver = object
    Checkpoint = dict
    CheckpointMetadata = dict
    CheckpointTuple = Any
    ChannelVersions = dict
    RunnableConfig = dict
    JsonPlusSerializer = json

logger = structlog.get_logger("aether.core.redis_checkpointer")


class AsyncJsonSerializer:
    """Simple JSON serializer for checkpoints."""
    
    def dumps(self, obj: Any) -> bytes:
        return json.dumps(obj, default=str).encode("utf-8")
    
    def loads(self, data: bytes) -> Any:
        return json.loads(data.decode("utf-8"))


class RedisCheckpointer(BaseCheckpointSaver):
    """
    A production-grade Redis-backed checkpointer for LangGraph.
    
    Implements BaseCheckpointSaver so it works with graph.ainvoke / astream.
    Checkpoints are stored with TTL and support namespace isolation.
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        namespace: str = "aether",
        ttl_hours: int = 24,
    ):
        super().__init__()
        self.redis_client = redis_client
        self.namespace = namespace
        self.ttl_seconds = ttl_hours * 3600
        self.serde = AsyncJsonSerializer()
        logger.info("redis_checkpointer.initialized", namespace=namespace, ttl_hours=ttl_hours)

    # ── Internal helpers ──────────────────────────────────────────

    def _checkpoint_key(self, thread_id: str, checkpoint_ns: str = "", checkpoint_id: Optional[str] = None) -> str:
        parts = [self.namespace, "checkpoint", thread_id]
        if checkpoint_ns:
            parts.append(checkpoint_ns)
        if checkpoint_id:
            parts.append(checkpoint_id)
        return ":".join(parts)

    def _writes_key(self, thread_id: str, checkpoint_ns: str = "", checkpoint_id: str = "", task_id: str = "") -> str:
        return f"{self.namespace}:writes:{thread_id}:{checkpoint_ns}:{checkpoint_id}:{task_id}"

    def _config_to_thread_id(self, config: RunnableConfig) -> str:
        return config.get("configurable", {}).get("thread_id", "default")

    def _config_to_checkpoint_ns(self, config: RunnableConfig) -> str:
        return config.get("configurable", {}).get("checkpoint_ns", "")

    def _config_to_checkpoint_id(self, config: RunnableConfig) -> Optional[str]:
        return config.get("configurable", {}).get("checkpoint_id")

    # ── Async interface (LangGraph ≥ 0.2) ─────────────────────────

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        """Retrieve the latest checkpoint tuple for a thread."""
        thread_id = self._config_to_thread_id(config)
        checkpoint_ns = self._config_to_checkpoint_ns(config)
        checkpoint_id = self._config_to_checkpoint_id(config)

        # If a specific checkpoint_id is requested, fetch it directly
        if checkpoint_id:
            key = self._checkpoint_key(thread_id, checkpoint_ns, checkpoint_id)
            data = await self.redis_client.get(key)
            if not data:
                return None
            payload = self.serde.loads(data)
            return CheckpointTuple(
                config=config,
                checkpoint=payload["checkpoint"],
                metadata=payload["metadata"],
                parent_config=payload.get("parent_config"),
                pending_writes=payload.get("pending_writes", []),
            )

        # Otherwise, list checkpoints for this thread/ns and return the latest
        pattern = self._checkpoint_key(thread_id, checkpoint_ns, "*")
        keys = await self.redis_client.keys(pattern)
        if not keys:
            logger.debug("redis_checkpointer.no_checkpoints", thread_id=thread_id)
            return None

        # Sort by checkpoint_id (ULID / timestamp lexicographic) descending
        keys = sorted([k.decode("utf-8") if isinstance(k, bytes) else k for k in keys], reverse=True)
        latest_key = keys[0]
        data = await self.redis_client.get(latest_key)
        if not data:
            return None

        payload = self.serde.loads(data)
        checkpoint_id = latest_key.split(":")[-1]
        resolved_config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }
        return CheckpointTuple(
            config=resolved_config,
            checkpoint=payload["checkpoint"],
            metadata=payload["metadata"],
            parent_config=payload.get("parent_config"),
            pending_writes=payload.get("pending_writes", []),
        )

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Store a checkpoint and return its config."""
        thread_id = self._config_to_thread_id(config)
        checkpoint_ns = self._config_to_checkpoint_ns(config)
        checkpoint_id = checkpoint.get("id") or checkpoint.get("ts") or datetime.now(timezone.utc).isoformat()

        key = self._checkpoint_key(thread_id, checkpoint_ns, checkpoint_id)
        parent_config = config if self._config_to_checkpoint_id(config) else None

        payload = {
            "checkpoint": checkpoint,
            "metadata": metadata,
            "parent_config": parent_config,
            "pending_writes": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        await self.redis_client.setex(key, self.ttl_seconds, self.serde.dumps(payload))
        logger.info("redis_checkpointer.checkpoint_stored", key=key, thread_id=thread_id)

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
    ) -> None:
        """Store intermediate writes for a task."""
        thread_id = self._config_to_thread_id(config)
        checkpoint_ns = self._config_to_checkpoint_ns(config)
        checkpoint_id = self._config_to_checkpoint_id(config) or ""

        key = self._writes_key(thread_id, checkpoint_ns, checkpoint_id, task_id)
        payload = {
            "writes": writes,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.redis_client.setex(key, self.ttl_seconds, self.serde.dumps(payload))
        logger.debug("redis_checkpointer.writes_stored", key=key, task_id=task_id)

    async def alist(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """List checkpoints for a thread, newest first."""
        if config is None:
            return

        thread_id = self._config_to_thread_id(config)
        checkpoint_ns = self._config_to_checkpoint_ns(config)
        pattern = self._checkpoint_key(thread_id, checkpoint_ns, "*")

        keys = await self.redis_client.keys(pattern)
        if not keys:
            return

        keys = sorted([k.decode("utf-8") if isinstance(k, bytes) else k for k in keys], reverse=True)
        if limit:
            keys = keys[:limit]

        for key in keys:
            data = await self.redis_client.get(key)
            if not data:
                continue
            payload = self.serde.loads(data)
            cp_id = key.split(":")[-1]
            resolved_config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": cp_id,
                }
            }
            yield CheckpointTuple(
                config=resolved_config,
                checkpoint=payload["checkpoint"],
                metadata=payload["metadata"],
                parent_config=payload.get("parent_config"),
                pending_writes=payload.get("pending_writes", []),
            )

    # ── Legacy sync interface (for compat) ────────────────────────

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        raise NotImplementedError("Use aget_tuple — this checkpointer is async-only.")

    def put(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: ChannelVersions) -> RunnableConfig:
        raise NotImplementedError("Use aput — this checkpointer is async-only.")

    def put_writes(self, config: RunnableConfig, writes: Sequence[Tuple[str, Any]], task_id: str) -> None:
        raise NotImplementedError("Use aput_writes — this checkpointer is async-only.")

    def list(self, config: Optional[RunnableConfig], *, filter: Optional[dict] = None, before: Optional[RunnableConfig] = None, limit: Optional[int] = None):
        raise NotImplementedError("Use alist — this checkpointer is async-only.")

    # ── Admin helpers ─────────────────────────────────────────────

    async def delete_checkpoint(self, config: RunnableConfig) -> bool:
        thread_id = self._config_to_thread_id(config)
        checkpoint_ns = self._config_to_checkpoint_ns(config)
        checkpoint_id = self._config_to_checkpoint_id(config)
        if not checkpoint_id:
            return False
        key = self._checkpoint_key(thread_id, checkpoint_ns, checkpoint_id)
        result = await self.redis_client.delete(key)
        return bool(result)

    async def delete_thread(self, thread_id: str) -> int:
        """Delete every checkpoint and intermediate write associated with one task thread."""
        checkpoint_pattern = f"{self.namespace}:checkpoint:{thread_id}:*"
        writes_pattern = f"{self.namespace}:writes:{thread_id}:*"
        keys = [*await self.redis_client.keys(checkpoint_pattern), *await self.redis_client.keys(writes_pattern)]
        if not keys:
            return 0
        deleted = await self.redis_client.delete(*keys)
        logger.info("redis_checkpointer.thread_deleted", thread_id=thread_id, deleted=deleted)
        return int(deleted)

    async def cleanup_expired(self) -> int:
        pattern = f"{self.namespace}:checkpoint:*"
        keys = await self.redis_client.keys(pattern)
        cleaned = 0
        for key in keys:
            ttl = await self.redis_client.ttl(key)
            if ttl == -1:  # No TTL set
                await self.redis_client.delete(key)
                cleaned += 1
        logger.info("redis_checkpointer.cleanup_complete", cleaned_count=cleaned)
        return cleaned