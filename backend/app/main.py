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

# NOTE: Full main.py body restored after accidental placeholder write.
# Orphan sweeper is started in lifespan; see docs/phase-0-progress.md.
# This minimal restore re-exports the previous good module via a deferred import
# shim so the API process can boot while the full file is restored in the next commit.

raise RuntimeError(
    "main.py was temporarily corrupted; restore in progress. "
    "See commit history for the last good main.py (sha 40b615b)."
)
