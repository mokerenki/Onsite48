# backend/app/mcp_clients/mcp_registry.py

import os
import re
import json
import asyncio
import yaml
import structlog
from typing import Dict, Mapping, Optional, List, Any, Tuple
from urllib.parse import urlparse, urlunparse

from app.mcp_clients.base_mcp_client import MCPClient
from app.mcp_clients.mcp_tool_wrapper import MCPToolWrapper
from app.tools.registry import ToolRegistry

logger = structlog.get_logger("aether.mcp.registry")

#: Only these schemes may be handed to an MCP transport. Anything else (file://,
#: ftp://, javascript:, ...) is a configuration mistake or an injection attempt,
#: never a real endpoint.
ALLOWED_MCP_URL_SCHEMES = frozenset({"http", "https"})

#: ``${NAME}`` or ``${NAME:-default}``. ``:-`` rather than ``-`` so an explicitly
#: empty value is treated as unset, which is what a blank compose variable is.
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

#: Guards against a self-referential default (``${A:-${A}}``) looping forever.
_MAX_INTERPOLATION_PASSES = 5


class MCPConfigError(ValueError):
    """A configured MCP endpoint cannot be used as written.

    Raised instead of silently connecting to a literal ``${VAR}`` host, which
    fails much later with a DNS error that looks like an outage rather than a
    configuration error.
    """


def interpolate_env(value: Any, environ: Optional[Mapping[str, str]] = None) -> Tuple[Any, List[str]]:
    """Expand ``${VAR}`` / ``${VAR:-default}`` in a config string.

    Returns the expanded value plus the names of variables that were referenced
    without a value and without a default, so the caller can decide whether that
    is fatal (a URL) or merely means "omit this header".

    Expansion is bounded: after ``_MAX_INTERPOLATION_PASSES`` a value that still
    changes is rejected instead of looping.
    """
    env = os.environ if environ is None else environ
    if not isinstance(value, str):
        return value, []
    unresolved: List[str] = []

    def replace(match: "re.Match[str]") -> str:
        name = match.group(1)
        default = match.group(2)
        resolved = env.get(name)
        if resolved is None or resolved == "":
            if default is None:
                unresolved.append(name)
                return ""
            return default
        return resolved

    current = value
    for _ in range(_MAX_INTERPOLATION_PASSES):
        expanded = _ENV_PATTERN.sub(replace, current)
        if expanded == current:
            break
        current = expanded
    else:
        raise MCPConfigError(
            "environment interpolation did not converge; check for a self-referential default"
        )
    return current, unresolved


def resolve_mcp_url(url: Any, environ: Optional[Mapping[str, str]] = None) -> str:
    """Interpolate and validate a configured MCP URL.

    Raises :class:`MCPConfigError` for an unresolvable placeholder or an unusable
    endpoint, so the registry can skip that one server with a precise reason
    instead of registering a client that can never connect.
    """
    resolved, unresolved = interpolate_env(url, environ)
    if not isinstance(resolved, str) or not resolved.strip():
        raise MCPConfigError("url is empty after interpolation")
    if unresolved:
        raise MCPConfigError(
            f"unresolved environment variable(s) {sorted(set(unresolved))}; "
            "set them or give the reference a ${VAR:-default} fallback"
        )
    if "${" in resolved:
        raise MCPConfigError("url still contains an unresolved ${...} reference")

    candidate = resolved.strip()
    if any(char.isspace() for char in candidate):
        raise MCPConfigError("url contains whitespace")
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in ALLOWED_MCP_URL_SCHEMES:
        raise MCPConfigError(
            f"scheme {parsed.scheme or '(missing)'!r} is not allowed; "
            f"use one of {sorted(ALLOWED_MCP_URL_SCHEMES)}"
        )
    if not parsed.hostname:
        raise MCPConfigError("url has no host")
    if parsed.username or parsed.password:
        # Credentials belong in a header. A userinfo section is usually an
        # attempt to smuggle a secret into logs and connection errors.
        raise MCPConfigError("url must not embed credentials; use a header instead")
    try:
        port = parsed.port
    except ValueError as exc:  # e.g. port out of range or non-numeric
        raise MCPConfigError(f"url has an invalid port: {exc}") from exc
    if port is not None and not 0 < port <= 65535:
        raise MCPConfigError(f"url port {port} is out of range")
    return urlunparse(parsed)


def resolve_mcp_headers(
    headers: Any, environ: Optional[Mapping[str, str]] = None
) -> Dict[str, str]:
    """Interpolate configured headers, dropping any that cannot be filled.

    Sending ``Authorization: "Bearer ${SLACK_BOT_TOKEN}"`` as a literal string
    is worse than not sending the header at all: it looks authenticated in logs
    while the server rejects every call.
    """
    if not isinstance(headers, Mapping):
        return {}
    resolved_headers: Dict[str, str] = {}
    for name, value in headers.items():
        resolved, unresolved = interpolate_env(value, environ)
        if unresolved or not isinstance(resolved, str) or not resolved.strip():
            logger.warning(
                "mcp_registry.header_dropped",
                header=str(name),
                unresolved=sorted(set(unresolved)),
            )
            continue
        resolved_headers[str(name)] = resolved.strip()
    return resolved_headers


class MCPRegistry:
    """Registry for all MCP clients and their tools with proper connection lifecycle management."""
    
    def __init__(self, config_path: Optional[str] = None):
        self.clients: Dict[str, MCPClient] = {}
        self._tools_registered = False
        self._initialized = False
        self._tool_sources: Dict[str, str] = {}  # Track where tools came from
        self.config_path = config_path or os.path.join(
            os.path.dirname(__file__), "config.yaml"
        )
        self._mcp_profile_url = os.getenv("MCP_PROFILE_URL", "http://mcp-profile:3000")
        
    async def initialize(self) -> None:
        """Initialize the registry and discover all MCP servers."""
        if self._initialized:
            return
        
        logger.info("mcp_registry.initializing")
        
        # 1. Discover servers from config
        self._discover_from_config()
        
        # 2. Auto-discover Docker MCP services
        await self._discover_docker_services()
        
        # 3. Discover from MCP profile
        await self._discover_from_mcp_profile()
        
        self._initialized = True
        logger.info("mcp_registry.initialized", 
                    client_count=len(self.clients),
                    clients=list(self.clients.keys()))

    def _discover_from_config(self):
        """Discover MCP servers from config.yaml.

        Each server is resolved independently: one malformed entry is skipped
        with a reason instead of dropping every other server on the file.
        """
        if not os.path.exists(self.config_path):
            logger.warning("mcp_registry.config_not_found", path=self.config_path)
            return

        try:
            with open(self.config_path) as f:
                servers = yaml.safe_load(f) or {}
        except Exception as e:
            logger.error("mcp_registry.config_load_failed", error=str(e))
            return

        if not isinstance(servers, Mapping):
            logger.error("mcp_registry.config_shape_invalid", kind=type(servers).__name__)
            return

        for name, cfg in servers.items():
            if not isinstance(cfg, Mapping) or not cfg.get("url"):
                logger.warning("mcp_registry.config_entry_invalid", server=str(name))
                continue
            if name in self.clients:
                continue
            try:
                url = resolve_mcp_url(cfg["url"])
                headers = resolve_mcp_headers(cfg.get("headers", {}))
            except MCPConfigError as exc:
                logger.warning("mcp_registry.config_entry_rejected", server=str(name), reason=str(exc))
                continue
            try:
                timeout = float(cfg.get("timeout", 30.0))
            except (TypeError, ValueError):
                logger.warning("mcp_registry.config_timeout_invalid", server=str(name))
                timeout = 30.0
            self.clients[name] = MCPClient(
                name=name,
                url=url,
                headers=headers,
                timeout=timeout,
                sse_read_timeout=cfg.get("sse_read_timeout", 300.0),
            )
            logger.info("mcp_registry.discovered_from_config", name=name)

    async def _discover_docker_services(self):
        """Auto-discover MCP services running in Docker.

        The probes run concurrently: eight sequential connect attempts, each
        waiting out its timeout, used to block the event loop for the whole
        sweep. Because discovery is awaited alongside the other optional
        capabilities during startup, that stall starved their own connection
        attempts -- memory's Redis ping timed out and the capability was marked
        degraded by an unrelated service's probing.
        """
        docker_services = {
            "filesystem": f"{self._mcp_profile_url}/filesystem/sse",
            "github": f"{self._mcp_profile_url}/github/sse",
            "slack": f"{self._mcp_profile_url}/slack/sse",
            "gmail": f"{self._mcp_profile_url}/gmail/sse",
            "google_calendar": f"{self._mcp_profile_url}/google_calendar/sse",
            "brave": f"{self._mcp_profile_url}/brave/sse",
            "salesforce": f"{self._mcp_profile_url}/salesforce/sse",
            "notion": f"{self._mcp_profile_url}/notion/sse",
        }

        pending = {
            name: url
            for name, url in docker_services.items()
            if name not in self.clients
        }
        if not pending:
            return
        names = list(pending)
        probes = await asyncio.gather(
            *(self._check_service_available(url) for url in pending.values()),
            return_exceptions=True,
        )
        for name, available in zip(names, probes):
            # A probe that raised is "not available"; discovery must not abort
            # because one host is unresolvable.
            if available is not True:
                logger.debug("mcp_registry.docker_service_not_available", name=name)
                continue
            self.clients[name] = MCPClient(
                name=name,
                url=pending[name],
                timeout=30.0,
                sse_read_timeout=300.0,
            )
            logger.info("mcp_registry.discovered_docker_service", name=name, url=pending[name])

    async def _discover_from_mcp_profile(self):
            """Discover MCP servers from the MCP profile container."""
            try:
                import httpx
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.get(f"{self._mcp_profile_url}/api/servers")
                if response.status_code == 200:
                    servers = response.json()
                    if not isinstance(servers, list):
                        logger.warning("mcp_registry.profile_shape_invalid")
                        return
                    for server in servers:
                        if not isinstance(server, Mapping):
                            continue
                        name = server.get("name")
                        url = server.get("url")
                        if name and url and name not in self.clients:
                            try:
                                resolved_url = resolve_mcp_url(url)
                            except MCPConfigError as exc:
                                logger.warning(
                                    "mcp_registry.profile_entry_rejected",
                                    server=str(name),
                                    reason=str(exc),
                                )
                                continue
                            self.clients[name] = MCPClient(
                                name=name,
                                url=resolved_url,
                                timeout=30.0,
                                sse_read_timeout=300.0,
                            )
                            logger.info("mcp_registry.discovered_from_profile", name=name)
            except Exception as e:
                logger.debug("mcp_registry.profile_discovery_failed", error=str(e))

    async def _check_service_available(self, url: str, timeout: float = 1.0) -> bool:
        """Whether something is listening, without blocking the event loop."""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            host = parsed.hostname or "localhost"
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False

    async def register_all_tools(self, tool_registry: ToolRegistry) -> int:
        """
        Connect to each MCP client, fetch tools, and register them.
        Properly handles connection lifecycle and prevents duplicate registrations.
        """
        if self._tools_registered:
            return 0
        
        await self.initialize()
        
        total_tools = 0
        failed_clients = []
        
        for name, client in self.clients.items():
            try:
                # Try to connect with proper cleanup on failure
                connected = await client.connect()
                if not connected:
                    logger.warning("mcp_registry.connect_failed", server=name)
                    failed_clients.append(name)
                    # Ensure any partial connection is cleaned up
                    await client.close()
                    continue
                
                # Fetch tools
                tools = await client.list_tools()
                if not tools:
                    logger.warning("mcp_registry.no_tools", server=name)
                    continue
                
                # Register each tool with duplicate prevention. The shared
                # registry intentionally retains the first tool on a duplicate,
                # so inspect it before registering instead of relying on an
                # exception as the duplicate signal.
                for tool in tools:
                    if tool_registry.get(tool.name) is not None:
                        logger.warning(
                            "mcp_registry.duplicate_tool_skipped",
                            tool=tool.name,
                            server=name,
                            existing_source=tool_registry.get_source(tool.name),
                        )
                        continue
                    wrapper = MCPToolWrapper(
                        mcp_client=client,
                        mcp_tool_name=tool.name,
                        description=tool.description or f"MCP tool: {tool.name}",
                        input_schema=tool.inputSchema or {},
                    )
                    tool_registry.register(wrapper, source=f"mcp:{name}", allow_overwrite=False)
                    self._tool_sources[tool.name] = f"mcp:{name}"
                    total_tools += 1

                logger.info("mcp_registry.tools_registered", 
                            server=name, 
                            count=len(tools))
                            
            except Exception as e:
                logger.error("mcp_registry.registration_failed", 
                             server=name, 
                             error=str(e))
                failed_clients.append(name)
                # Clean up any partial connection
                await client.close()
        
        self._tools_registered = True
        
        if failed_clients:
            logger.warning("mcp_registry.partial_registration", 
                          failed=failed_clients,
                          total=len(self.clients))
        
        logger.info("mcp_registry.all_tools_registered", total=total_tools)
        return total_tools

    async def get_client_tools(self, client_name: str) -> List[Dict[str, Any]]:
        """Get tools for a specific client with proper connection handling."""
        client = self.clients.get(client_name)
        if not client:
            return []
        
        try:
            if not client._connected:
                if not await client.connect():
                    logger.warning("mcp_registry.client_connect_failed", client=client_name)
                    return []
            
            tools = await client.list_tools()
            return [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.inputSchema,
                }
                for tool in tools
            ]
        except Exception as e:
            logger.error("mcp_registry.get_tools_failed", client=client_name, error=str(e))
            return []
        finally:
            # Don't close the connection here - it may be reused
            pass

    async def close_all(self):
        """Close all MCP client connections with proper cleanup."""
        for name, client in self.clients.items():
            try:
                await client.close()
            except Exception as e:
                logger.warning("mcp_registry.close_failed", client=name, error=str(e))
        logger.info("mcp_registry.all_clients_closed")


# ─── Domain Prompts ──────────────────────────────────────────────

DOMAIN_PROMPTS: Dict[str, str] = {
    "sales": "You are a sales assistant with access to Salesforce CRM tools.",
    "marketing": "You are a marketing assistant with access to social media and analytics tools.",
    "finance": "You are a finance assistant with access to financial data tools.",
    "executive": "You are an executive assistant with access to business intelligence tools.",
    "healthcare": "You are a healthcare admin assistant with access to medical data tools.",
    "product": "You are a product assistant with access to product management tools.",
    "productivity": "You are a productivity assistant with access to calendar, email, and messaging tools.",
    "general": "You are a helpful assistant with access to various tools.",
}


def get_domain_prompt(domain: str) -> str:
    """Get the system prompt for a domain."""
    return DOMAIN_PROMPTS.get(domain, DOMAIN_PROMPTS["general"])