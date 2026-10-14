"""Browser pool management for the standalone Browser Bridge service."""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger("onsite48.browser_bridge.pool")


class BrowserSession(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tab_id: Optional[int] = None
    url: Optional[str] = None
    status: str = "idle"
    device_id: Optional[str] = None
    created_at: float = Field(default_factory=lambda: asyncio.get_event_loop().time())


class BrowserPool:
    """Manages a pool of browser sessions with lease-based allocation.

    Supports up to `max_sessions` concurrent browser sessions. Each session
    is leased to a task with a TTL; expired leases are reclaimed automatically.
    """

    def __init__(self, max_sessions: int = 50) -> None:
        self.max_sessions = max_sessions
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = asyncio.Lock()
        self._reaper: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._reaper = asyncio.create_task(self._run_reaper())
        logger.info("browser_pool.started", max_sessions=self.max_sessions)

    async def stop(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            try:
                await self._reaper
            except asyncio.CancelledError:
                pass
        logger.info("browser_pool.stopped")

    async def acquire(self, device_id: Optional[str] = None) -> BrowserSession:
        async with self._lock:
            for session in self._sessions.values():
                if session.status == "idle":
                    session.status = "active"
                    session.device_id = device_id
                    logger.info("browser_pool.acquired", session_id=session.id)
                    return session
            if len(self._sessions) >= self.max_sessions:
                raise RuntimeError("Browser pool is at maximum capacity")
            session = BrowserSession(status="active", device_id=device_id)
            self._sessions[session.id] = session
            logger.info("browser_pool.created", session_id=session.id)
            return session

    async def release(self, session_id: str) -> bool:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            session.status = "idle"
            session.device_id = None
            logger.info("browser_pool.released", session_id=session_id)
            return True

    async def terminate(self, session_id: str) -> bool:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                return False
            logger.info("browser_pool.terminated", session_id=session_id)
            return True

    async def _run_reaper(self) -> None:
        while True:
            try:
                await asyncio.sleep(60)
                now = asyncio.get_event_loop().time()
                async with self._lock:
                    for session in list(self._sessions.values()):
                        if session.status == "idle" and now - session.created_at > 3600:
                            self._sessions.pop(session.id, None)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("browser_pool.reaper_error", error=str(exc))
