import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from dotenv import load_dotenv

# Load .env from the project root (sibling of backend/) before any os.getenv() reads.
# Docker already injects these via env_file, but local dev needs this so the
# model router and Config see NVIDIA_API_KEY / KIMI_API_KEY / DEEPSEEK_API_KEY
# without relying on the shell having them set.
# Load missing model keys from .env without overwriting Compose-injected
# dependency settings such as POSTGRES_HOST, REDIS_URL, and QDRANT_HOST.
_project_root = Path(__file__).resolve().parent.parent.parent
_env_path = _project_root / ".env"
if _env_path.exists():
    load_dotenv(_env_path, override=False)
else:
    load_dotenv(override=False)
# from app.tools.browser_control_tool import BrowserControlTool
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from sqlalchemy import select, text

from app.agents.codeact import CodeActTool
from app.agents.router import DomainRouter
from app.api.auth import router as auth_router
from app.api.browser_bridge import bridge_websocket_endpoint
from app.api.browser_bridge import router as browser_bridge_router
from app.api.browser_sessions import router as browser_sessions_router
from app.api.chat_automation import router as chat_automation_router
from app.api.command_center import router as command_center_router
from app.api.computer import router as computer_router
from app.api.connectors import router as connectors_router
from app.api.custom_connectors import router as custom_connectors_router
from app.api.files import router as files_router
from app.api.library import router as library_router
from app.api.notifications import router as notifications_router
from app.api.operator import router as operator_router
from app.api.platform import router as platform_router
from app.api.proactive import router as proactive_router
from app.api.proactive_engine import router as proactive_engine_router
from app.api.product_tasks import router as product_tasks_router
from app.api.rbac import router as rbac_router
from app.api.realtime import secure_websocket_endpoint
from app.api.sandbox_api import router as sandbox_router
from app.api.tasks_replay import router as tasks_replay_router
from app.api.web_previews import router as web_previews_router
from app.core import instances
from app.core.config import CONFIG, settings
from app.core.context import onsite48
from app.core.model_router_kimi_deepseek import KimiDeepSeekRouter
from app.core.observability import setup_logging, setup_telemetry
from app.core.platform_metrics import metrics as platform_metrics_registry
from app.core.proactive_scheduler import ProactiveScheduler
from app.core.readiness import (
    DISABLED,
    FAILED,
    OK,
    WARMING,
    FeatureCheck,
    readiness_payload,
)
from app.core.redis_checkpointer import RedisCheckpointer
from app.core.security import get_websocket_user
from app.core.tracing import TRACE_HEADER, bind_request_trace
from app.db import close_database, get_session_factory, initialize_database
from app.mcp_clients.mcp_registry import MCPRegistry
from app.memory.cognee_setup import CogneeMemory
from app.memory.retriever_tool import MemoryRetrieverTool
from app.models import OrganizationMember
from app.sandbox.persistent_manager import persistent_sandbox_manager
from app.services.agent_computer import AgentComputerManager
from app.services.browser_automation_service import BrowserAutomationService
from app.services.browser_bridge import BrowserBridgeService
from app.services.browser_live_registry import _set_redis_registry, browser_live_registry
from app.services.browser_session import BrowserSessionService

# Legacy bridge_* tools remain importable for internal/compat use but are NOT registered
# into the model-visible tool registry (tool surface collapse — protocol v3.1).
from app.services.browser_session_router import BrowserSessionRouter
from app.services.capability_catalog import register_business_capabilities
from app.services.proactive_worker import ProactiveOperationsWorker
from app.services.visual_grounding import VisualGroundingService
from app.tools.browser_tool import BrowserTool
from app.tools.calendar_tool import CalendarTool
from app.tools.chart_visualization_tool import ChartVisualizationTool
from app.tools.computer_tools import (
    ComputerBackTool,
    ComputerClickAtTool,
    ComputerClickTool,
    ComputerFillTool,
    ComputerInspectTool,
    ComputerLocateTool,
    ComputerNavigateTool,
    ComputerPressKeyTool,
    ComputerReadPageTool,
    ComputerScrollTool,
    ComputerTypeAtTool,
)
from app.tools.current_time import CurrentTimeTool
from app.tools.document_tool import DocumentTool
from app.tools.email_tool import EmailTool
from app.tools.library_tools import LibrarySearchTool
from app.tools.my_browser_tools import register_my_browser_tools
from app.tools.python_repl import PythonREPLTool
from app.tools.registry import ToolRegistry
from app.tools.slack_tool import SlackTool
from app.tools.whatsapp_tool import WhatsAppTool
from app.tools.wide_research_tool import SpawnSubagentTool, WideResearchTool

logger = structlog.get_logger("aether.main")

setup_logging()
_tracing_enabled = setup_telemetry()


async def _warm_optional_services(app: FastAPI) -> None:
    """Warm optional capabilities concurrently without blocking L1 readiness."""
    async def warm_correction_store() -> None:
        try:
            from app.services.correction_store import correction_store
            await correction_store.initialize()
            app.state.correction_store = correction_store
            app.state.capability_readiness["corrections"] = "ready"
            logger.info("optional_service.ready", service="corrections")
        except Exception as exc:
            app.state.capability_readiness["corrections"] = "degraded"
            logger.warning("optional_service.degraded", service="corrections", error=str(exc)[:240])

    async def warm_memory() -> None:
        try:
            await app.state.cognee_memory.initialize()
            app.state.capability_readiness["memory"] = "ready"
            logger.info("optional_service.ready", service="memory")
        except Exception as exc:
            app.state.capability_readiness["memory"] = "degraded"
            logger.warning("optional_service.degraded", service="memory", error=str(exc)[:240])

    async def warm_mcp() -> None:
        try:
            await app.state.mcp_registry.initialize()
            await app.state.mcp_registry.register_all_tools(app.state.tool_registry)
            app.state.capability_readiness["mcp"] = "ready"
            logger.info("optional_service.ready", service="mcp")
        except Exception as exc:
            app.state.capability_readiness["mcp"] = "degraded"
            logger.warning("optional_service.degraded", service="mcp", error=str(exc)[:240])

    async def warm_sandbox() -> None:
        try:
            await persistent_sandbox_manager.initialize()
            app.state.capability_readiness["persistent_sandbox"] = "ready"
            logger.info("optional_service.ready", service="persistent_sandbox")
        except Exception as exc:
            app.state.capability_readiness["persistent_sandbox"] = "degraded"
            logger.warning("optional_service.degraded", service="persistent_sandbox", error=str(exc)[:240])

    await asyncio.gather(warm_memory(), warm_mcp(), warm_sandbox(), warm_correction_store())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Context manager for managing the lifespan of the FastAPI application.
    Initializes and cleans up resources like Redis, CogneeMemory, and ProactiveScheduler.
    """
    logger.info("application.startup", environment=CONFIG.environment)
    CONFIG.validate()
    app.state.capability_readiness = {"core": "starting", "memory": "warming", "mcp": "warming", "persistent_sandbox": "warming"}
    app.state.optional_service_tasks = set()
    if os.getenv("AUTO_CREATE_SCHEMA", "false").lower() == "true":
        await initialize_database()
        logger.warning("database.schema_created_automatically", message="Use Alembic migrations in production.")

    # Initialize Redis client for checkpointer, task queue, and embedding cache
    app.state.redis_client = aioredis.from_url(settings.redis_url)
    await app.state.redis_client.ping()
    logger.info("redis.connected")

    # Initialize Redis-backed browser live registry (Phase 0)
    if os.environ.get("USE_REDIS_WS_REGISTRY", "").lower() in ("1", "true", "yes"):
        _set_redis_registry(app.state.redis_client)
        await browser_live_registry.start()
        logger.info("browser_live_registry.initialized", backend="redis")
    else:
        logger.info("browser_live_registry.initialized", backend="memory")

    # Initialize Kimi/DeepSeek Model Router
    app.state.model_router = KimiDeepSeekRouter()
    logger.info("model_router.initialized")

    # Initialize Redis Checkpointer (PRODUCTION-GRADE — survives restarts)
    app.state.checkpointer = RedisCheckpointer(
        redis_client=app.state.redis_client,
        namespace="aether",
        ttl_hours=24,
    )
    logger.info("checkpointer.initialized", type="redis")

    # Initialize Cognee Memory (with Redis embedding cache)
    app.state.cognee_memory = CogneeMemory(config={
        "qdrant_host": os.getenv("QDRANT_HOST", "qdrant"),
        "qdrant_port": int(os.getenv("QDRANT_PORT", "6333")),
        # A new scoped graph avoids silently trusting legacy unscoped Kuzu rows.
        "kuzu_db_path": os.getenv("KUZU_DB_PATH", "/tmp/onsite48/kuzu_scoped_v2.db"),
        "embedding_model_name": os.getenv("OPENAI_EMBEDDING_MODEL", "nvidia/nv-embed-v1"),
        "llm_extraction_model_name": os.getenv("OPENAI_CHAT_MODEL", "meta/llama-3.3-70b-instruct"),
        "redis_url": settings.redis_url,
    })
    logger.info("cognee_memory.deferred_until_after_core_readiness")

    # Initialize Browser Automation Service
    app.state.browser_service = BrowserAutomationService()
    await app.state.browser_service.start()
    app.state.visual_grounding = VisualGroundingService(app.state.model_router)
    app.state.agent_computer = AgentComputerManager(app.state.browser_service, visual_grounding=app.state.visual_grounding)
    app.state.browser_bridge = BrowserBridgeService(get_session_factory())
    app.state.browser_sessions = BrowserSessionService(get_session_factory())
    logger.info("browser_service.initialized")

    # ------------------- TOOL REGISTRY & MCP -------------------
    app.state.tool_registry = ToolRegistry()
    app.state.browser_search_tool = BrowserTool()
    app.state.tool_registry.register(app.state.browser_search_tool)
    app.state.tool_registry.register(WideResearchTool(app.state.browser_search_tool, app.state.model_router))
    app.state.tool_registry.register(SpawnSubagentTool(app.state.model_router, app.state.tool_registry))
    app.state.tool_registry.register(ComputerNavigateTool(app.state.agent_computer))
    app.state.tool_registry.register(ComputerBackTool(app.state.agent_computer))
    app.state.tool_registry.register(ComputerScrollTool(app.state.agent_computer))
    app.state.tool_registry.register(ComputerReadPageTool(app.state.agent_computer))
    app.state.tool_registry.register(ComputerInspectTool(app.state.agent_computer))
    app.state.tool_registry.register(ComputerLocateTool(app.state.agent_computer))
    app.state.tool_registry.register(ComputerClickTool(app.state.agent_computer))
    app.state.tool_registry.register(ComputerClickAtTool(app.state.agent_computer))
    app.state.tool_registry.register(ComputerFillTool(app.state.agent_computer))
    app.state.tool_registry.register(ComputerTypeAtTool(app.state.agent_computer))
    app.state.tool_registry.register(ComputerPressKeyTool(app.state.agent_computer))
    # My Browser: small model-visible set only (browser_observe / act / takeover / stop)
    app.state.browser_session_router = BrowserSessionRouter(
        bridge=app.state.browser_bridge,
        computer_manager=getattr(app.state, "agent_computer", None),
        browser_session_service=getattr(app.state, "browser_session_service", None),
    )
    register_my_browser_tools(
        app.state.tool_registry,
        app.state.browser_bridge,
        session_router=app.state.browser_session_router,
    )
    app.state.tool_registry.register(LibrarySearchTool())
    app.state.tool_registry.register(PythonREPLTool())
    app.state.tool_registry.register(CodeActTool(app.state.model_router, tool_registry=app.state.tool_registry))
    app.state.tool_registry.register(WhatsAppTool())
    app.state.tool_registry.register(MemoryRetrieverTool(app.state.cognee_memory))
    app.state.tool_registry.register(CurrentTimeTool())
    app.state.tool_registry.register(EmailTool())
    app.state.tool_registry.register(CalendarTool())
    app.state.tool_registry.register(DocumentTool())
    # app.state.tool_registry.register(BrowserControlTool())
    app.state.tool_registry.register(SlackTool())
    # All tenant-scoped connector and custom-MCP capabilities are assembled
    # through one catalog shared with background execution. Authority is decided
    # later by the run policy, not by which process created the registry.
    register_business_capabilities(app.state.tool_registry)
    logger.info("builtin_tools.registered", count=len(app.state.tool_registry.list_tools()))

    app.state.tool_registry.register(ChartVisualizationTool())
    logger.info("chart_visualization_tool.registered")

    # app.state.tool_registry.register(BrowserControlTool())
    # logger.info("browser_control_tool.registered")

    app.state.mcp_registry = MCPRegistry()
    # The domain router can serve with the empty MCP registry while optional
    # discovery runs in the background; newly discovered tools are registered
    # into the same registry and become available to subsequent plans.
    app.state.domain_router = DomainRouter(
        model_router=app.state.model_router,
        mcp_registry=app.state.mcp_registry
    )
    logger.info("domain_router.initialized")

    # Proactive work must be organization-scoped and consented to before it
    # is enabled. The legacy global scheduler remains off by default.
    if os.getenv("ENABLE_PROACTIVE_WORKFLOWS", "false").lower() == "true":
        app.state.proactive_scheduler = ProactiveScheduler(
            model_router=app.state.model_router,
            cognee_memory=app.state.cognee_memory,
            redis_client=app.state.redis_client,
            briefing_time_str=os.getenv("MORNING_BRIEFING_TIME", "08:00"),
        )
        asyncio.create_task(app.state.proactive_scheduler.start())
        logger.warning("proactive_scheduler.started", message="Only enable after organization-scoped consent and task ownership are configured.")

    # User-controlled proactive briefings run in a separate process in Compose.
    # Keeping this opt-in prevents duplicate schedules if the API is scaled.
    if os.getenv("RUN_PROACTIVE_WORKER", "false").lower() == "true":
        app.state.proactive_worker = ProactiveOperationsWorker(
            get_session_factory(),
            poll_seconds=int(os.getenv("PROACTIVE_WORKER_POLL_SECONDS", "60")),
            redis_client=app.state.redis_client,
            checkpointer=app.state.checkpointer,
            model_router=app.state.model_router,
            tool_registry=app.state.tool_registry,
            domain_router=app.state.domain_router,
        )
        await app.state.proactive_worker.start()
    else:
        logger.info("proactive_worker.disabled_in_api_process")

    app.state.capability_readiness["core"] = "ready"
    app.state.optional_service_tasks.add(asyncio.create_task(_warm_optional_services(app)))
    logger.info("application.l1_ready", deferred=["memory", "mcp", "persistent_sandbox"])

    # ------------------- POPULATE GLOBAL CONTEXT -------------------
    # THIS IS THE KEY CHANGE - one initialization to rule them all
    onsite48.initialize(
        redis_client=app.state.redis_client,
        model_router=app.state.model_router,
        checkpointer=app.state.checkpointer,
        cognee_memory=app.state.cognee_memory,
        browser_service=app.state.browser_service,
        tool_registry=app.state.tool_registry,
        mcp_registry=app.state.mcp_registry,
        domain_router=app.state.domain_router,
        started_at=datetime.now(UTC).isoformat(),
    )
    logger.info("onsite48_context.initialized")

    # Store in app state; the manager is warmed by _warm_optional_services.
    app.state.persistent_sandbox_manager = persistent_sandbox_manager

    # Also update legacy instances for backward compatibility
    instances.tool_registry = app.state.tool_registry
    instances.mcp_registry = app.state.mcp_registry
    instances.domain_router = app.state.domain_router
    instances.model_router = app.state.model_router
    instances.checkpointer = app.state.checkpointer
    instances.cognee_memory = app.state.cognee_memory

    # Phase 4: Load YAML policy engine (synchronous, fast)
    from app.runtime.policy_engine import get_policy_engine
    get_policy_engine()

    # Phase 5: Initialize Temporal dual-write shadow mode (non-blocking)
    from app.memory.temporal_workflows import get_temporal_dual_write
    _temporal = get_temporal_dual_write()
    if _temporal.enabled:
        asyncio.create_task(_temporal.initialize())

    # Phase 5: Initialize proactive signal engine
    from app.services.signal_engine import get_signal_engine
    _signal_engine = get_signal_engine()
    asyncio.create_task(_signal_engine.initialize())

    yield

    # ------------------- CLEANUP -------------------
    logger.info("application.shutdown")
    # Stop the Redis WebSocket registry listener if it was started
    try:
        await browser_live_registry.stop()
    except Exception:
        pass
    for task in getattr(app.state, "optional_service_tasks", set()):
        if not task.done():
            task.cancel()
    if getattr(app.state, "optional_service_tasks", None):
        await asyncio.gather(*app.state.optional_service_tasks, return_exceptions=True)
    
    # Use onsite48.close() for unified cleanup
    await onsite48.close()
    
    if hasattr(app.state, 'proactive_worker'):
        await app.state.proactive_worker.stop()
        logger.info("proactive_worker.stopped")

    # Also clean up legacy proactive scheduler separately (it's not in onsite48)
    if hasattr(app.state, 'proactive_scheduler'):
        await app.state.proactive_scheduler.stop()
        logger.info("proactive_scheduler.stopped")
    
    if hasattr(app.state, 'mcp_registry'):
        await app.state.mcp_registry.close_all()
        logger.info("mcp_registry.closed")

    await close_database()
    logger.info("application.shutdown_complete")


app = FastAPI(
    lifespan=lifespan,
    title="Onsite48 Agent Platform",
    description="Secure, specialized autonomous agents for business workflows.",
    version="1.0.0"
)

# CORS Middleware
cors_origins = list(CONFIG.cors_origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# Additional CORS for Chrome Extension (chrome-extension://*)
# Bridge endpoints are called from the extension which has an unknown origin at deploy time
class ChromeExtensionCORSMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        # Only apply to bridge API endpoints
        if path.startswith("/api/bridge"):
            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    # Allow any chrome-extension origin
                    headers.append((b"access-control-allow-origin", b"*"))
                    headers.append((b"access-control-allow-credentials", b"true"))
                    headers.append((b"access-control-allow-methods", b"GET, POST, PUT, DELETE, OPTIONS"))
                    headers.append((b"access-control-allow-headers", b"*"))
                    message["headers"] = headers
                elif message["type"] == "http.response.body" and scope.get("method") == "OPTIONS":
                    # Handle preflight
                    pass
                await send(message)
            await self.app(scope, receive, send_wrapper)
        else:
            await self.app(scope, receive, send)

app.add_middleware(ChromeExtensionCORSMiddleware)

@app.middleware("http")
async def structured_request_logging(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    # Reused by the error handlers so a client-visible incident id maps to the
    # sanitized server-side log line that holds the real detail.
    request.state.request_id = request_id
    # Bind the trace before anything downstream can open a span. Without this
    # `span()` mints an id lazily and a single request that produces several
    # spans can end up with a different trace per span, so the trace cannot be
    # reassembled and there is no id to quote to a user reporting a problem.
    trace_id = bind_request_trace(request.headers)
    request.state.trace_id = trace_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "http.request_failed",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        raise
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    # Echoed so the identifier a user can quote and the identifier in the server
    # log are the same string, rather than two that have to be correlated by
    # timestamp.
    response.headers[TRACE_HEADER] = trace_id
    logger.info(
        "http.request_completed",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    return response


# Include API routers
app.include_router(auth_router)
app.include_router(product_tasks_router)
app.include_router(connectors_router)
app.include_router(custom_connectors_router)
app.include_router(files_router)
app.include_router(web_previews_router)
app.include_router(computer_router)
app.include_router(browser_bridge_router)
app.include_router(browser_sessions_router)
app.include_router(library_router)
app.include_router(command_center_router)
app.include_router(notifications_router)
app.include_router(proactive_router)
app.include_router(proactive_engine_router)
app.include_router(chat_automation_router)
app.include_router(operator_router)
app.include_router(platform_router)
app.include_router(rbac_router)
app.include_router(sandbox_router)
app.include_router(tasks_replay_router)
# WebSocket endpoint
@app.websocket("/ws/bridge")
async def bridge_websocket_route(websocket: WebSocket):
    await bridge_websocket_endpoint(websocket, app.state.browser_bridge, app.state.browser_sessions)


@app.websocket("/ws")
async def websocket_route(websocket: WebSocket):
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            user = await get_websocket_user(websocket, session)
            membership = await session.scalar(
                select(OrganizationMember)
                .where(OrganizationMember.user_id == user.id)
                .order_by(OrganizationMember.created_at.asc())
            )
        except HTTPException:
            membership = None
        if membership is None:
            await websocket.close(code=4401, reason="Authentication required")
            return
        await secure_websocket_endpoint(
            websocket,
            user=user,
            membership=membership,
            session_factory=session_factory,
            checkpointer=app.state.checkpointer,
            model_router=app.state.model_router,
            browser_service=app.state.browser_service,
            tool_registry=app.state.tool_registry,
            domain_router=app.state.domain_router,
        )


@app.get("/", response_class=HTMLResponse)
async def read_root():
    return "<h1>Onsite48 Agent Platform</h1>"


@app.get("/api/health", tags=["monitoring"])
async def health_check():
    """Fast liveness probe that does not require dependency round trips."""
    return {"status": "ok", "service": "onsite48-api", "environment": CONFIG.environment}


def _dependency_failure(exc: Exception, *, dependency: str) -> dict[str, str]:
    """Report a dependency failure without echoing internals to the client.

    ``/api/health/detailed`` is unauthenticated, and ``str(exc)`` on a driver or
    client error routinely carries hostnames, ports, DSN fragments and index or
    relation names. The exception class is enough to separate a connection
    refusal from a timeout; the full text stays in the server log.
    """
    logger.warning("health.dependency_error", dependency=dependency, error=str(exc))
    return {"status": "error", "detail": type(exc).__name__}


@app.get("/api/health/ready", tags=["monitoring"])
async def readiness_check() -> JSONResponse:
    """Per-feature readiness. Only a required feature failing makes this 503.

    `/api/health/detailed` returned a single boolean over every dependency, so a
    Qdrant outage returned 503 for the whole process and a load balancer
    reacting to that removed an instance that could still have served chat, task
    submission and auth perfectly well.

    Here each feature is reported separately and tagged required or optional.
    A failing optional feature is listed under `degraded_optional` and does not
    change the status code, so a real outage stays visible without taking healthy
    features offline with it. A feature that is switched off reports `disabled`,
    which is deliberately not the same as `error`.
    """
    checks: list[FeatureCheck] = []

    try:
        async with get_session_factory()() as session:
            await session.execute(text("SELECT 1"))
        checks.append(FeatureCheck("database", OK, required=True))
    except Exception as exc:
        checks.append(FeatureCheck(
            "database", FAILED, required=True, detail=type(exc).__name__
        ))
        _log_dependency_failure(exc, dependency="database")

    try:
        await app.state.redis_client.ping()
        checks.append(FeatureCheck("redis", OK, required=True))
    except Exception as exc:
        checks.append(FeatureCheck(
            "redis", FAILED, required=True, detail=type(exc).__name__
        ))
        _log_dependency_failure(exc, dependency="redis")

    checks.extend(await _migration_check())
    checks.append(_qdrant_check())

    browser_service = getattr(app.state, "browser_service", None)
    browser_ready = bool(browser_service and getattr(browser_service, "browser", None))
    checks.append(FeatureCheck(
        "browser",
        OK if browser_ready else FAILED,
        required=False,
        detail="" if browser_ready else "Browser automation service is not initialized.",
    ))

    checks.append(await _worker_check())

    readiness = await _capability_readiness()
    checks.append(FeatureCheck(
        "capabilities",
        OK if readiness.get("core") == "ready" else WARMING,
        required=False,
        detail=f"core={readiness.get('core', 'unknown')}",
    ))

    payload = readiness_payload(checks, environment=CONFIG.environment)
    return JSONResponse(
        status_code=200 if payload["ready"] else 503, content=payload
    )


def _log_dependency_failure(exc: Exception, *, dependency: str) -> None:
    logger.warning("health.dependency_error", dependency=dependency, error=str(exc))


def _qdrant_check() -> FeatureCheck:
    """Probe the vector store.

    Memory recall is optional: the agent still plans, executes and answers
    without it, so this never gates readiness. It is probed because the previous
    endpoint reported a `memory` readiness figure derived from configuration
    while never confirming the store was actually reachable -- a `memory: ready`
    with a dead Qdrant is worse than no report, because it is believed.
    """
    if not CONFIG.memory.cognee_enabled:
        return FeatureCheck(
            "qdrant", DISABLED, required=False, detail="COGNEE_ENABLED is false"
        )
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(
            host=CONFIG.memory.qdrant_host, port=CONFIG.memory.qdrant_port, timeout=2.0
        )
        collections = client.get_collections()
        client.close()
        return FeatureCheck(
            "qdrant", OK, required=False, detail=f"{len(collections.collections)} collections"
        )
    except Exception as exc:
        _log_dependency_failure(exc, dependency="qdrant")
        return FeatureCheck("qdrant", FAILED, required=False, detail=type(exc).__name__)


async def _migration_check() -> list[FeatureCheck]:
    """A process serving against a schema behind the code fails on first write.

    Unlike the other checks this is about our own state rather than a
    dependency's, so it is required.
    """
    try:
        from alembic.config import Config as AlembicConfig
        from alembic.script import ScriptDirectory

        from app.core.config import CONFIG as _CONFIG  # noqa: F401  (import guard)

        backend_root = Path(__file__).resolve().parent.parent
        config = AlembicConfig(str(backend_root / "alembic.ini"))
        config.set_main_option("script_location", str(backend_root / "alembic"))
        head = ScriptDirectory.from_config(config).get_current_head()
        async with get_session_factory()() as session:
            recorded = await session.scalar(text("SELECT version_num FROM alembic_version"))
        if recorded is None:
            return [FeatureCheck(
                "migrations", FAILED, required=True, detail="alembic_version is empty"
            )]
        if head and recorded != head:
            return [FeatureCheck(
                "migrations", FAILED, required=True,
                detail=f"database at {recorded}, code at {head}",
            )]
        return [FeatureCheck("migrations", OK, required=True)]
    except Exception as exc:
        _log_dependency_failure(exc, dependency="migrations")
        return [FeatureCheck(
            "migrations", FAILED, required=True, detail=type(exc).__name__
        )]


async def _worker_check() -> FeatureCheck:
    """The proactive worker is deployed independently of the API, so its
    absence here is not a fault of this process."""
    worker = getattr(app.state, "proactive_worker", None)
    if worker:
        status = worker.status()
        return FeatureCheck(
            "proactive_worker",
            OK if status.get("running") else FAILED,
            required=False,
            detail=f"running={bool(status.get('running'))}",
        )
    try:
        heartbeat = await app.state.redis_client.get("onsite48:proactive_worker:heartbeat")
    except Exception as exc:
        _log_dependency_failure(exc, dependency="proactive_worker")
        return FeatureCheck("proactive_worker", FAILED, required=False, detail=type(exc).__name__)
    if not heartbeat:
        return FeatureCheck(
            "proactive_worker", DISABLED, required=False,
            detail="no heartbeat; worker not deployed",
        )
    return FeatureCheck("proactive_worker", OK, required=False)


async def _capability_readiness() -> dict[str, Any]:
    """`app.state.capability_readiness` is a dict the lifespan handlers mutate
    as subsystems finish warming. It is a liveness signal, not a probe: a
    subsystem that crashed after warming still reads `ready` here, which is why
    the qdrant check above exists separately."""
    state = getattr(app.state, "capability_readiness", None)
    if not isinstance(state, dict):
        return {"core": "unknown", "memory": "unknown", "mcp": "unknown",
                "persistent_sandbox": "unknown"}
    return state


@app.get("/api/health/detailed", tags=["monitoring"])
async def detailed_health_check() -> JSONResponse:
    """Readiness probe for the production dependencies required by Onsite48."""
    checks: dict[str, dict[str, object]] = {}

    try:
        async with get_session_factory()() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception as exc:
        checks["database"] = _dependency_failure(exc, dependency="database")

    try:
        await app.state.redis_client.ping()
        checks["redis"] = {"status": "ok"}
    except Exception as exc:
        checks["redis"] = _dependency_failure(exc, dependency="redis")

    browser_service = getattr(app.state, "browser_service", None)
    browser_ready = bool(browser_service and getattr(browser_service, "browser", None))
    checks["browser"] = {"status": "ok" if browser_ready else "error"}
    if not browser_ready:
        checks["browser"]["detail"] = "Browser automation service is not initialized."

    worker = getattr(app.state, "proactive_worker", None)
    if worker:
        worker_status = worker.status()
    else:
        try:
            heartbeat = await app.state.redis_client.get("onsite48:proactive_worker:heartbeat")
            heartbeat_value = heartbeat.decode() if isinstance(heartbeat, bytes) else heartbeat
            worker_status = {"running": bool(heartbeat_value), "last_heartbeat_at": heartbeat_value}
        except Exception as exc:
            logger.warning("health.worker_heartbeat_error", error=str(exc))
            worker_status = {"running": False, "last_error": type(exc).__name__}
    checks["proactive_worker"] = {
        "status": "ok" if worker_status["running"] else "error",
        **worker_status,
    }
    readiness = getattr(app.state, "capability_readiness", {})
    checks["capabilities"] = {
        "status": "ok" if readiness.get("core") == "ready" else "warming",
        "core": readiness.get("core", "unknown"),
        "memory": readiness.get("memory", "unknown"),
        "mcp": readiness.get("mcp", "unknown"),
        "persistent_sandbox": readiness.get("persistent_sandbox", "unknown"),
    }

    # L1 readiness is intentionally separate from optional capabilities and
    # from the independently deployed proactive worker heartbeat.
    healthy = checks.get("database", {}).get("status") == "ok" and checks.get("redis", {}).get("status") == "ok" and checks.get("capabilities", {}).get("status") == "ok"
    payload = {
        "status": "ok" if healthy else "degraded",
        "service": "onsite48-api",
        "environment": CONFIG.environment,
        "checks": checks,
    }
    return JSONResponse(status_code=200 if healthy else 503, content=payload)


@app.get("/api/metrics", tags=["monitoring"], response_class=PlainTextResponse)
async def metrics() -> str:
    """Minimal Prometheus-compatible gauges without exposing user data."""
    worker = getattr(app.state, "proactive_worker", None)
    if worker:
        worker_status = worker.status()
    else:
        try:
            heartbeat = await app.state.redis_client.get("onsite48:proactive_worker:heartbeat")
            worker_status = {
                "running": bool(heartbeat),
                "processed_count": 0,
                "failed_count": 0,
                "dead_letter_count": 0,
            }
        except Exception:
            worker_status = {
                "running": False,
                "processed_count": 0,
                "failed_count": 0,
                "dead_letter_count": 0,
            }
    browser_service = getattr(app.state, "browser_service", None)
    browser_ready = int(bool(browser_service and getattr(browser_service, "browser", None)))
    lines = [
        "# HELP onsite48_up Whether the API process is serving requests.",
        "# TYPE onsite48_up gauge",
        "onsite48_up 1",
        "# HELP onsite48_browser_ready Whether the browser automation service is initialized.",
        "# TYPE onsite48_browser_ready gauge",
        f"onsite48_browser_ready {browser_ready}",
        "# HELP onsite48_proactive_worker_running Whether the proactive worker is running.",
        "# TYPE onsite48_proactive_worker_running gauge",
        f"onsite48_proactive_worker_running {int(bool(worker_status['running']))}",
        "# HELP onsite48_proactive_worker_processed_total Successful proactive subscriptions processed since startup.",
        "# TYPE onsite48_proactive_worker_processed_total counter",
        f"onsite48_proactive_worker_processed_total {int(worker_status['processed_count'])}",
        "# HELP onsite48_proactive_worker_failed_total Failed proactive subscription attempts since startup.",
        "# TYPE onsite48_proactive_worker_failed_total counter",
        f"onsite48_proactive_worker_failed_total {int(worker_status['failed_count'])}",
        "# HELP onsite48_proactive_worker_dead_letter_total Subscriptions dead-lettered since startup.",
        "# TYPE onsite48_proactive_worker_dead_letter_total counter",
        f"onsite48_proactive_worker_dead_letter_total {int(worker_status['dead_letter_count'])}",
    ]
    return "\\n".join(lines) + "\\n" + platform_metrics_registry.text()


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.error("aether.http_exception", status_code=exc.status_code, detail=exc.detail, path=request.url.path)
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": exc.detail},
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    # Exception text can carry DSNs, file paths, SQL fragments and upstream
    # provider payloads. Clients get a correlation id; the detail stays in the
    # sanitized server log under that same id.
    request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    logger.error("aether.general_exception", request_id=request_id, error=str(exc), path=request.url.path, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"message": "An unexpected error occurred.", "request_id": request_id},
    )
