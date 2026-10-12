"""
Legacy instances module - delegates to new context.
Kept for backward compatibility during migration.
"""

from typing import Optional
from app.tools.registry import ToolRegistry
from app.mcp_clients.mcp_registry import MCPRegistry
from app.agents.router import DomainRouter
from app.core.model_router_kimi_deepseek import KimiDeepSeekRouter
from app.core.redis_checkpointer import RedisCheckpointer
from app.core.context import onsite48

# These will be populated by main.py during startup
tool_registry: Optional[ToolRegistry] = None
mcp_registry: Optional[MCPRegistry] = None
domain_router: Optional[DomainRouter] = None
model_router: Optional[KimiDeepSeekRouter] = None
checkpointer: Optional[RedisCheckpointer] = None
cognee_memory = None

def get_context():
    """Get the Onsite48 context. Use this during migration."""
    return onsite48

def ensure_initialized():
    """Forward to context."""
    onsite48.ensure_initialized()