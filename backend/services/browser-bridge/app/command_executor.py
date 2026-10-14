"""Command execution for the standalone Browser Bridge service."""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger("onsite48.browser_bridge.executor")


class CommandResult(BaseModel):
    command_id: str
    status: str = "pending"
    result: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    completed_at: Optional[float] = None


class CommandExecutor:
    """Executes browser commands and tracks their results.

    Commands are dispatched to extension handlers which communicate with
    the actual browser extension over the bridge protocol.
    """

    def __init__(self) -> None:
        self._results: dict[str, CommandResult] = {}
        self._lock = asyncio.Lock()

    async def stop(self) -> None:
        logger.info("command_executor.stopped")

    async def submit(
        self,
        command_type: str,
        payload: dict[str, Any],
        *,
        device_id: str,
        session_id: Optional[str] = None,
    ) -> str:
        command_id = str(uuid.uuid4())
        result = CommandResult(command_id=command_id)
        async with self._lock:
            self._results[command_id] = result
        logger.info(
            "command.submitted",
            command_id=command_id,
            command_type=command_type,
            device_id=device_id,
            session_id=session_id,
        )
        return command_id

    async def complete(
        self,
        command_id: str,
        *,
        result: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> bool:
        async with self._lock:
            entry = self._results.get(command_id)
            if entry is None:
                return False
            entry.result = result or {}
            entry.error = error
            entry.status = "failed" if error else "completed"
            entry.completed_at = asyncio.get_event_loop().time()
        logger.info("command.completed", command_id=command_id, status=entry.status)
        return True

    async def get_result(self, command_id: str) -> Optional[CommandResult]:
        async with self._lock:
            return self._results.get(command_id)
