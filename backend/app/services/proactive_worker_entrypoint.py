"""Dedicated process entrypoint for durable scheduled agent work."""

from __future__ import annotations

import asyncio
import os
import signal
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis
import structlog

from app.agents.router import DomainRouter
from app.core.config import CONFIG, settings
from app.mcp_clients.mcp_registry import MCPRegistry
from app.core.context import onsite48
from app.core.model_router_kimi_deepseek import KimiDeepSeekRouter
from app.core.redis_checkpointer import RedisCheckpointer
from app.db import close_database, get_session_factory
from app.services.agent_computer import AgentComputerManager
from app.services.browser_automation_service import BrowserAutomationService
from app.services.proactive_worker import ProactiveOperationsWorker
from app.services.capability_catalog import register_business_capabilities
from app.services.visual_grounding import VisualGroundingService
from app.tools.browser_tool import BrowserTool
from app.tools.computer_tools import (
    ComputerBackTool, ComputerClickAtTool, ComputerClickTool, ComputerFillTool,
    ComputerInspectTool, ComputerLocateTool, ComputerNavigateTool,
    ComputerPressKeyTool, ComputerReadPageTool, ComputerScrollTool, ComputerTypeAtTool,
)
from app.tools.current_time import CurrentTimeTool
from app.tools.registry import ToolRegistry
from app.tools.wide_research_tool import WideResearchTool

logger = structlog.get_logger("onsite48.proactive_worker_process")


def build_worker_runtime(
    *,
    redis_client: Any,
    model_router: KimiDeepSeekRouter | None = None,
    browser_service: BrowserAutomationService | None = None,
    mcp_registry: MCPRegistry | None = None,
) -> dict[str, Any]:
    """Create the automated task runtime with the same tenant-scoped connector surface.

    Scheduled work never receives a local-browser bridge. Read-only connected
    tools can execute with the schedule owner's identity; drafting, mutations,
    and opaque remote MCP calls remain confirmation-gated by their own schemas.
    """
    router = model_router or KimiDeepSeekRouter()
    service = browser_service or BrowserAutomationService()
    visual_grounding = VisualGroundingService(router)
    computer = AgentComputerManager(service, visual_grounding=visual_grounding)
    registry = ToolRegistry()
    browser_search = BrowserTool()
    registry.register(browser_search)
    registry.register(WideResearchTool(browser_search, router))
    registry.register(ComputerNavigateTool(computer))
    registry.register(ComputerBackTool(computer))
    registry.register(ComputerScrollTool(computer))
    registry.register(ComputerReadPageTool(computer))
    registry.register(ComputerInspectTool(computer))
    registry.register(ComputerLocateTool(computer))
    registry.register(ComputerClickTool(computer))
    registry.register(ComputerClickAtTool(computer))
    registry.register(ComputerFillTool(computer))
    registry.register(ComputerTypeAtTool(computer))
    registry.register(ComputerPressKeyTool(computer))
    registry.register(CurrentTimeTool())

    # Use the same tenant-scoped business connector and custom-MCP capability
    # surface as interactive execution. The schedule policy controls whether a
    # specific action runs unattended; tool omission is never the policy layer.
    register_business_capabilities(registry)

    active_mcp_registry = mcp_registry or MCPRegistry()
    return {
        "model_router": router,
        "browser_service": service,
        "tool_registry": registry,
        "mcp_registry": active_mcp_registry,
        "checkpointer": RedisCheckpointer(redis_client=redis_client, namespace="aether", ttl_hours=24),
        "domain_router": DomainRouter(router, active_mcp_registry),
    }


async def register_worker_mcp_tools(runtime: dict[str, Any]) -> int:
    """Discover and register configured MCP tools for the worker runtime.

    ``MCPToolWrapper`` marks every discovered MCP tool confirmation-gated, so
    a schedule can inspect/use its real MCP surface but cannot execute opaque
    third-party code without a durable user approval in the task workspace.
    """
    mcp_registry: MCPRegistry = runtime["mcp_registry"]
    await mcp_registry.initialize()
    registered = await mcp_registry.register_all_tools(runtime["tool_registry"])
    logger.info(
        "proactive_worker.mcp_tools_registered",
        registered=registered,
        available_tools=len(runtime["tool_registry"].list_tools()),
    )
    return registered


async def run() -> None:
    CONFIG.validate()
    redis_client = aioredis.from_url(settings.redis_url)
    await redis_client.ping()
    runtime = build_worker_runtime(redis_client=redis_client)
    await register_worker_mcp_tools(runtime)
    await runtime["browser_service"].start()
    # Scheduled tasks deliberately reuse the canonical task event stream so
    # their task history, checkpoints, notifications, and approval pauses are
    # identical to interactive work. That stream persists progress through the
    # global context normally initialized by FastAPI lifespan; initialize the
    # same worker-owned runtime here before the first claimed schedule runs.
    onsite48.initialize(
        redis_client=redis_client,
        model_router=runtime["model_router"],
        checkpointer=runtime["checkpointer"],
        browser_service=runtime["browser_service"],
        tool_registry=runtime["tool_registry"],
        mcp_registry=runtime["mcp_registry"],
        domain_router=runtime["domain_router"],
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    logger.info("proactive_worker.context_initialized")
    worker = ProactiveOperationsWorker(
        get_session_factory(),
        poll_seconds=int(os.getenv("PROACTIVE_WORKER_POLL_SECONDS", "60")),
        redis_client=redis_client,
        checkpointer=runtime["checkpointer"],
        model_router=runtime["model_router"],
        tool_registry=runtime["tool_registry"],
        domain_router=runtime["domain_router"],
    )
    await worker.start()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_shutdown() -> None:
        logger.info("proactive_worker.shutdown_requested")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_shutdown)
        except NotImplementedError:
            signal.signal(sig, lambda *_: request_shutdown())

    try:
        await stop_event.wait()
    finally:
        await worker.stop()
        await runtime["browser_service"].stop()
        await runtime["mcp_registry"].close_all()
        await runtime["model_router"].close()
        await redis_client.aclose()
        await close_database()
        logger.info("proactive_worker.shutdown_complete")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()