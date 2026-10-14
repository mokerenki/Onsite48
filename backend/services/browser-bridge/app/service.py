"""Standalone Browser Bridge service extracted from the monolith.

Phase 0: Provides isolated browser session management for large-scale
deployments. The monolith's BrowserBridgeService remains as the source of
truth for session state; this service handles command execution, browser pool
management, and extension protocol handling at scale.
"""
from __future__ import annotations

from .browser_pool import BrowserPool
from .command_executor import CommandExecutor
from .extension_handler import ExtensionHandler

__all__ = ["BrowserBridgeService", "BrowserPool", "CommandExecutor", "ExtensionHandler"]


class BrowserBridgeService:
    """Standalone browser bridge service.

    Manages browser sessions, command execution, and extension communication
    independently of the main API process.
    """

    def __init__(self, max_sessions: int = 50) -> None:
        self.max_sessions = max_sessions
        self.pool = BrowserPool(max_sessions=max_sessions)
        self.executor = CommandExecutor()
        self.handler = ExtensionHandler(self.pool, self.executor)

    async def start(self) -> None:
        await self.pool.start()

    async def stop(self) -> None:
        await self.pool.stop()
        await self.executor.stop()
