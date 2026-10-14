"""Low-latency fanout for authenticated live browser session updates.

The durable BrowserSession and BrowserSessionEvent tables remain the source of
truth. This registry is only an optimization for connected task workspaces;
REST session refresh provides replay/recovery after worker or socket loss.

Phase 0: Redis-backed pub/sub for horizontal scaling across multiple API pods.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any

import structlog
from fastapi import WebSocket

from app.core.platform_metrics import metrics

logger = structlog.get_logger("onsite48.browser_live_registry")

# Feature flag: fall back to in-memory when Redis is unavailable or disabled.
USE_REDIS = os.environ.get("USE_REDIS_WS_REGISTRY", "").lower() in ("1", "true", "yes")


class RedisBrowserRegistry:
    """WebSocket fanout backed by Redis pub/sub.

    Local subscribers are tracked per-process. Cross-pod broadcasts arrive via
    Redis pub/sub and are forwarded to locally subscribed sockets. The durable
    BrowserSession tables remain the source of truth for session state.
    """

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client
        self.pubsub = redis_client.pubsub() if redis_client is not None else None
        self._local_subscribers: dict[str, set[WebSocket]] = {}
        self._listen_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self.pubsub is not None:
            self._listen_task = asyncio.create_task(self._listen())
            logger.info("browser_live_registry.started", backend="redis")

    async def stop(self) -> None:
        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        if self.pubsub is not None:
            await self.pubsub.close()

    async def subscribe(self, session_id: uuid.UUID | str, websocket: WebSocket) -> None:
        sid = str(session_id)
        if self.pubsub is not None:
            async with self._lock:
                if sid not in self._local_subscribers:
                    self._local_subscribers[sid] = set()
                    await self.pubsub.subscribe(f"browser:{sid}")
                self._local_subscribers[sid].add(websocket)
            metrics.inc("onsite48_ws_subscriptions", labels={"backend": "redis"})
        else:
            async with self._lock:
                if sid not in self._local_subscribers:
                    self._local_subscribers[sid] = set()
                self._local_subscribers[sid].add(websocket)
            metrics.inc("onsite48_ws_subscriptions", labels={"backend": "memory"})

    async def unsubscribe(self, session_id: uuid.UUID | str, websocket: WebSocket) -> None:
        sid = str(session_id)
        async with self._lock:
            if sid in self._local_subscribers:
                self._local_subscribers[sid].discard(websocket)
                if not self._local_subscribers[sid]:
                    del self._local_subscribers[sid]
                    if self.pubsub is not None:
                        await self.pubsub.unsubscribe(f"browser:{sid}")

    async def unsubscribe_websocket(self, websocket: WebSocket) -> None:
        """Remove a websocket from all session subscriptions (disconnect path)."""
        async with self._lock:
            empty: list[str] = []
            for sid, subs in self._local_subscribers.items():
                subs.discard(websocket)
                if not subs:
                    empty.append(sid)
            for sid in empty:
                self._local_subscribers.pop(sid, None)
                if self.pubsub is not None:
                    await self.pubsub.unsubscribe(f"browser:{sid}")

    async def broadcast(self, session_id: uuid.UUID | str, event: dict[str, Any]) -> None:
        sid = str(session_id)
        payload = json.dumps(event)
        if self.redis is not None:
            await self.redis.publish(f"browser:{sid}", payload)
            metrics.inc("onsite48_ws_broadcast_redis")
        else:
            async with self._lock:
                subscribers = list(self._local_subscribers.get(sid, set()))
            for ws in subscribers:
                try:
                    await ws.send_json(event)
                except Exception:
                    pass
            metrics.inc("onsite48_ws_broadcast_local", value=len(subscribers))

    async def _listen(self) -> None:
        """Consume Redis pub/sub messages and forward to local subscribers."""
        if self.pubsub is None:
            return
        try:
            async for message in self.pubsub.listen():
                if message["type"] != "message":
                    continue
                channel = message.get("channel", "")
                if isinstance(channel, bytes):
                    channel = channel.decode()
                sid = channel.replace("browser:", "")
                try:
                    event = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    continue
                async with self._lock:
                    subscribers = list(self._local_subscribers.get(sid, set()))
                for ws in subscribers:
                    try:
                        await ws.send_json(event)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("browser_live_registry.listen_error", error=str(exc))


# Backward-compatible in-memory fallback (default when USE_REDIS_WS_REGISTRY is unset).
class BrowserLiveRegistry:
    """In-memory WebSocket fanout. Single-process only; used when Redis is disabled."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._subscribers: dict[str, set[WebSocket]] = {}

    async def start(self) -> None:
        """No-op for in-memory registry (compatibility with RedisBrowserRegistry)."""
        logger.info("browser_live_registry.started", backend="memory")

    async def stop(self) -> None:
        """No-op for in-memory registry (compatibility with RedisBrowserRegistry)."""
        pass

    async def subscribe(self, session_id: uuid.UUID | str, websocket: WebSocket) -> None:
        async with self._lock:
            self._subscribers.setdefault(str(session_id), set()).add(websocket)
        metrics.inc("onsite48_ws_subscriptions", labels={"backend": "memory"})

    async def unsubscribe(self, session_id: uuid.UUID | str, websocket: WebSocket) -> None:
        async with self._lock:
            key = str(session_id)
            if key in self._subscribers:
                self._subscribers[key].discard(websocket)
                if not self._subscribers[key]:
                    del self._subscribers[key]

    async def unsubscribe_websocket(self, websocket: WebSocket) -> None:
        async with self._lock:
            empty: list[str] = []
            for sid, subs in self._subscribers.items():
                subs.discard(websocket)
                if not subs:
                    empty.append(sid)
            for sid in empty:
                self._subscribers.pop(sid, None)

    async def broadcast(self, session_id: uuid.UUID | str, event: dict[str, Any]) -> None:
        key = str(session_id)
        async with self._lock:
            subscribers = list(self._subscribers.get(key, set()))
        for ws in subscribers:
            try:
                await ws.send_json(event)
            except Exception:
                pass
        metrics.inc("onsite48_ws_broadcast_local", value=len(subscribers))


# Global instance — initialized in main.py lifespan.
# When USE_REDIS_WS_REGISTRY is true, this becomes a RedisBrowserRegistry after
# main.py sets it up; otherwise it stays the in-memory BrowserLiveRegistry.
browser_live_registry: RedisBrowserRegistry | BrowserLiveRegistry = BrowserLiveRegistry()


def _set_redis_registry(redis_client: Any) -> None:
    """Replace the global registry with a Redis-backed one (called from main.py)."""
    global browser_live_registry
    browser_live_registry = RedisBrowserRegistry(redis_client)


__all__ = ["BrowserLiveRegistry", "RedisBrowserRegistry", "_set_redis_registry", "browser_live_registry"]
