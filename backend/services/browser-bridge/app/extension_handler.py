"""Extension protocol handling for the standalone Browser Bridge service."""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional

import structlog
from fastapi import WebSocket

from .browser_pool import BrowserPool
from .command_executor import CommandExecutor

logger = structlog.get_logger("onsite48.browser_bridge.extension")


class ExtensionHandler:
    """Manages WebSocket connections from browser extensions.

    Handles the authentication handshake, command dispatch, and event
    delivery protocol for browser extensions connecting to the bridge service.
    """

    def __init__(self, pool: BrowserPool, executor: CommandExecutor) -> None:
        self.pool = pool
        self.executor = executor
        self._connections: dict[str, WebSocket] = {}

    async def register(self, device_id: str, websocket: WebSocket) -> None:
        self._connections[device_id] = websocket
        logger.info("extension.registered", device_id=device_id)

    async def unregister(self, device_id: str) -> None:
        self._connections.pop(device_id, None)
        logger.info("extension.unregistered", device_id=device_id)

    async def dispatch_command(
        self,
        device_id: str,
        command_type: str,
        payload: dict[str, Any],
    ) -> str:
        command_id = await self.executor.submit(
            command_type, payload, device_id=device_id
        )
        ws = self._connections.get(device_id)
        if ws is not None:
            await ws.send_json({
                "type": "command_queued",
                "command_id": command_id,
                "command_type": command_type,
                "payload": payload,
            })
        return command_id

    async def handle_message(self, device_id: str, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        if event_type == "command_completed":
            await self.executor.complete(
                str(event.get("command_id") or ""),
                result=event.get("result"),
            )
        elif event_type == "command_failed":
            await self.executor.complete(
                str(event.get("command_id") or ""),
                error=event.get("error"),
            )
        else:
            logger.debug("extension.unhandled_event", device_id=device_id, event_type=event_type)
