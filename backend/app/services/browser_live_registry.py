"""Low-latency fanout for authenticated live browser session updates.

The durable BrowserSession and BrowserSessionEvent tables remain the source of
truth. This registry is only an optimization for connected task workspaces;
REST session refresh provides replay/recovery after worker or socket loss.
"""
from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class BrowserLiveRegistry:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._subscribers: dict[str, set[WebSocket]] = defaultdict(set)

    async def subscribe(self, session_id: uuid.UUID | str, websocket: WebSocket) -> None:
        async with self._lock:
            self._subscribers[str(session_id)].add(websocket)

    async def unsubscribe(self, websocket: WebSocket) -> None:
        async with self._lock:
            empty: list[str] = []
            for session_id, subscribers in self._subscribers.items():
                subscribers.discard(websocket)
                if not subscribers:
                    empty.append(session_id)
            for session_id in empty:
                self._subscribers.pop(session_id, None)

    async def broadcast(self, session_id: uuid.UUID | str, event: dict[str, Any]) -> None:
        async with self._lock:
            subscribers = list(self._subscribers.get(str(session_id), set()))
        stale: list[WebSocket] = []
        for websocket in subscribers:
            try:
                await websocket.send_json(event)
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            await self.unsubscribe(websocket)


browser_live_registry = BrowserLiveRegistry()

__all__ = ["BrowserLiveRegistry", "browser_live_registry"]