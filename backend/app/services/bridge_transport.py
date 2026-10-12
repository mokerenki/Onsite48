"""In-process transport registry for the authenticated Onsite48 Bridge socket.

This deployment runs one API process.  Device sockets and command-progress
queues therefore belong in process memory, keyed by the durable device/command
identifiers.  Database state remains authoritative: disconnecting a socket only
returns delivery to the existing polling fallback.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class BridgeTransportRegistry:
    """Own active device sockets and bounded per-command progress queues.

    The registry deliberately stores no browser contents, credentials, or task
    state. It only transports typed command envelopes and small operational
    events. Database command status remains the source of truth.
    """

    def __init__(self) -> None:
        self._connections: dict[uuid.UUID, WebSocket] = {}
        self._command_queues: dict[uuid.UUID, asyncio.Queue[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def register(self, device_id: uuid.UUID, websocket: WebSocket) -> None:
        """Register one active socket per paired device, replacing stale links."""
        previous: WebSocket | None = None
        async with self._lock:
            previous = self._connections.get(device_id)
            self._connections[device_id] = websocket
        if previous is not None and previous is not websocket:
            try:
                await previous.close(code=1012, reason="A newer Onsite48 Bridge connection replaced this one.")
            except Exception:
                pass

    async def unregister(self, device_id: uuid.UUID, websocket: WebSocket | None = None) -> None:
        async with self._lock:
            current = self._connections.get(device_id)
            if current is not None and (websocket is None or current is websocket):
                self._connections.pop(device_id, None)

    async def connected(self, device_id: uuid.UUID) -> bool:
        async with self._lock:
            return device_id in self._connections

    async def send_command(self, device_id: uuid.UUID, command: dict[str, Any]) -> bool:
        """Push one command to a live extension, returning False on fallback."""
        async with self._lock:
            websocket = self._connections.get(device_id)
        if websocket is None:
            return False
        try:
            await websocket.send_json({"type": "command_queued", "command": command})
            return True
        except Exception:
            await self.unregister(device_id, websocket)
            return False

    async def ensure_command_queue(self, command_id: uuid.UUID) -> asyncio.Queue[dict[str, Any]]:
        async with self._lock:
            queue = self._command_queues.get(command_id)
            if queue is None:
                queue = asyncio.Queue(maxsize=32)
                self._command_queues[command_id] = queue
            return queue

    async def drop_command_queue(self, command_id: uuid.UUID) -> None:
        async with self._lock:
            self._command_queues.pop(command_id, None)

    async def publish_event(self, command_id: uuid.UUID, event: dict[str, Any]) -> None:
        """Wake the owning command waiter without retaining an event history."""
        async with self._lock:
            queue = self._command_queues.get(command_id)
        if queue is None:
            return
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # Dropping non-terminal progress is safe; terminal state is always
            # read from the database by queue_and_wait.
            pass

    async def wait_for_event(self, command_id: uuid.UUID, timeout_seconds: float) -> dict[str, Any] | None:
        queue = await self.ensure_command_queue(command_id)
        try:
            return await asyncio.wait_for(queue.get(), timeout=max(0.01, timeout_seconds))
        except asyncio.TimeoutError:
            return None


    async def send_browser_event(self, device_id: uuid.UUID, event: dict[str, Any]) -> bool:
        """Push one browser event to a live extension."""
        async with self._lock:
            websocket = self._connections.get(device_id)
        if websocket is None:
            return False
        try:
            await websocket.send_json({"type": "browser_event", "event": event, "device_id": str(device_id)})
            return True
        except Exception:
            await self.unregister(device_id, websocket)
            return False


bridge_transport_registry = BridgeTransportRegistry()


__all__ = ["BridgeTransportRegistry", "bridge_transport_registry"]