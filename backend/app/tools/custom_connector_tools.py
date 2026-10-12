"""Runtime tools for user-created remote API and MCP connectors.

Custom connectors never become global process tools. Each invocation resolves the
connector through the current execution identity, decrypts only that user's
credential envelope, and applies the stored endpoint boundary. MCP servers are
opaque third-party programs, so every MCP call is confirmation-gated.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import select

from app.mcp_clients.base_mcp_client import MCPClient
from app.db import get_session_factory
from app.models import CustomConnector
from app.services.agent_computer import UnsafeBrowserTarget, validate_public_url
from app.services.connector_vault import decrypt_credentials
from app.services.execution_identity import get_execution_identity
from app.services.provider_errors import (
    provider_error_from_exception,
    provider_error_from_status,
    redact_secrets,
)
from app.tools.registry import BaseTool, ToolSchema


async def _connector_for(slug: str, kind: str | None = None) -> CustomConnector:
    identity = get_execution_identity()
    async with get_session_factory()() as session:
        query = select(CustomConnector).where(
            CustomConnector.organization_id == identity.organization_id,
            CustomConnector.user_id == identity.user_id,
            CustomConnector.slug == slug,
        )
        connector = await session.scalar(query)
        if connector is None or (kind and connector.kind != kind):
            raise RuntimeError("The requested custom connector is unavailable in this workspace.")
        if connector.status != "connected":
            raise RuntimeError("Test and connect this custom connector in Settings before using it in a task.")
        session.expunge(connector)
        return connector


async def _assert_remote_https(url: str) -> None:
    safe = validate_public_url(url)
    parsed = urlparse(safe)
    if parsed.scheme != "https" or not parsed.hostname:
        raise UnsafeBrowserTarget("Custom connector calls require a complete public HTTPS endpoint.")
    addresses = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: socket.getaddrinfo(parsed.hostname, None, type=socket.SOCK_STREAM),
    )
    for _family, _type, _proto, _canonname, sockaddr in addresses:
        address = ipaddress.ip_address(sockaddr[0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified:
            raise UnsafeBrowserTarget("Private, local, and internal network addresses are not permitted for custom connector calls.")


def _api_url(connector: CustomConnector, path: str) -> str:
    parsed = urlparse(path)
    if parsed.scheme or parsed.netloc or path.startswith("//") or ".." in parsed.path.split("/"):
        raise ValueError("Custom API paths must remain relative to the registered endpoint.")
    target = urljoin(f"{connector.base_url.rstrip('/')}/", path.lstrip("/"))
    if urlparse(target).scheme != "https" or urlparse(target).netloc != urlparse(connector.base_url).netloc:
        raise ValueError("Custom API paths may not leave the registered HTTPS origin.")
    return target


def _api_auth(connector: CustomConnector) -> tuple[dict[str, str], dict[str, str]]:
    config = connector.configuration or {}
    mode = str(config.get("auth_mode") or "none")
    credentials = decrypt_credentials(connector.encrypted_credentials) or {}
    secret = credentials.get("secret")
    headers: dict[str, str] = {"Accept": "application/json"}
    params: dict[str, str] = {}
    if mode == "none":
        return headers, params
    if not secret:
        raise RuntimeError("This custom API connector needs its secret to be entered again.")
    credential_name = str(config.get("credential_name") or "")
    if mode == "bearer":
        headers["Authorization"] = f"Bearer {secret}"
    elif mode in {"api_key_header", "custom_header"}:
        headers[credential_name] = str(secret)
    elif mode == "api_key_query":
        params[credential_name] = str(secret)
    else:
        raise RuntimeError("This custom API connector has an unsupported authentication configuration.")
    return headers, params


async def _custom_request(
    slug: str,
    method: str,
    target: str,
    *,
    follow_redirects: bool = False,
    **kwargs: Any,
) -> httpx.Response:
    """One provider call for a user-created API connector.

    Every status class and transport failure becomes the shared structured
    contract, so the executor's retry / replan / pause decision is the same
    whatever the connector returns. Previously `raise_for_status()` surfaced an
    ``HTTPStatusError`` whose text contained the request URL, which for the
    `api_key_query` auth mode contains the API key itself.
    """
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=follow_redirects) as client:
            response = await client.request(method, target, **kwargs)
    except Exception as exc:
        raise provider_error_from_exception(slug, exc) from None
    if response.status_code >= 400:
        raise provider_error_from_status(slug, response)
    return response


def _bounded_response(response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    output: Any
    if "json" in content_type.lower():
        try:
            output = response.json()
        except ValueError:
            output = response.text[:20_000]
    else:
        output = response.text[:20_000]
    serialized = json.dumps(output, default=str)
    if len(serialized) > 20_000:
        output = {"truncated": True, "preview": serialized[:20_000]}
    # The requested URL is echoed for traceability, but an `api_key_query`
    # connector carries its credential in the query string, so the URL is
    # redacted rather than returned verbatim.
    return {
        "status_code": response.status_code,
        "url": redact_secrets(str(response.url)),
        "data": output,
    }


class CustomConnectorListTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="custom_connector_list",
            description="List connected custom API and MCP connectors available to the signed-in user. Use this before calling a custom connector when its identifier is not already known.",
            parameters={},
        )

    async def execute(self, **_: Any) -> dict[str, Any]:
        identity = get_execution_identity()
        async with get_session_factory()() as session:
            connectors = list((await session.scalars(select(CustomConnector).where(
                CustomConnector.organization_id == identity.organization_id,
                CustomConnector.user_id == identity.user_id,
                CustomConnector.status == "connected",
            ).order_by(CustomConnector.name.asc()))).all())
        return {"connectors": [
            {
                "slug": connector.slug,
                "name": connector.name,
                "kind": connector.kind,
                "description": connector.description or "",
                "usage_guide": connector.usage_guide or "",
            }
            for connector in connectors
        ]}


class CustomApiGetTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="custom_api_get",
            description="Call a connected user-created custom API with a GET request. The path must remain inside the registered HTTPS origin. This tool cannot mutate external state.",
            parameters={
                "connector": {"type": "string", "description": "The custom API connector slug from custom_connector_list."},
                "path": {"type": "string", "description": "Relative request path, for example /v1/projects."},
                "query": {"type": "object", "description": "Optional string or numeric query parameters."},
            },
            required=["connector", "path"],
            runtime="connector",
            idempotency="read_only",
            timeout_seconds=30,
            max_attempts=2,
        )

    async def execute(self, connector: str, path: str, query: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        definition = await _connector_for(connector, kind="api")
        target = _api_url(definition, path)
        await _assert_remote_https(target)
        headers, base_params = _api_auth(definition)
        params = {**base_params, **{str(key): str(value) for key, value in (query or {}).items()}}
        response = await _custom_request(
            definition.slug, "GET", target, headers=headers, params=params
        )
        return _bounded_response(response)


class CustomApiMutationTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="custom_api_mutate",
            description="Perform an approved POST, PUT, PATCH, or DELETE request through a connected custom API. Always requires exact user confirmation and is never automatically retried.",
            parameters={
                "connector": {"type": "string", "description": "The custom API connector slug from custom_connector_list."},
                "method": {"type": "string", "enum": ["POST", "PUT", "PATCH", "DELETE"]},
                "path": {"type": "string", "description": "Relative request path inside the registered origin."},
                "body": {"type": "object", "description": "Optional JSON request body."},
                "query": {"type": "object", "description": "Optional string or numeric query parameters."},
            },
            required=["connector", "method", "path"],
            irreversible=True,
            runtime="connector",
            idempotency="non_idempotent_write",
            approval_required=True,
            timeout_seconds=30,
            max_attempts=1,
        )

    async def execute(self, connector: str, method: str, path: str, body: dict[str, Any] | None = None, query: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        normalized_method = method.upper()
        if normalized_method not in {"POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("Choose POST, PUT, PATCH, or DELETE for a custom API mutation.")
        definition = await _connector_for(connector, kind="api")
        target = _api_url(definition, path)
        await _assert_remote_https(target)
        headers, base_params = _api_auth(definition)
        params = {**base_params, **{str(key): str(value) for key, value in (query or {}).items()}}
        response = await _custom_request(
            definition.slug,
            normalized_method,
            target,
            headers=headers,
            params=params,
            json=body,
            # Redirects are not followed on a mutation: a 307 would replay the
            # request at a host the user never approved.
            follow_redirects=False,
        )
        return _bounded_response(response)


async def _mcp_client_for(slug: str) -> tuple[CustomConnector, MCPClient]:
    definition = await _connector_for(slug, kind="mcp")
    await _assert_remote_https(definition.base_url)
    credentials = decrypt_credentials(definition.encrypted_credentials) or {}
    headers = credentials.get("headers") if isinstance(credentials.get("headers"), dict) else {}
    client = MCPClient(name=definition.slug, url=definition.base_url, headers={str(key): str(value) for key, value in headers.items()})
    if not await client.connect():
        await client.close()
        raise RuntimeError("Synth could not connect to this custom MCP server.")
    return definition, client


class CustomMcpListToolsTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="custom_mcp_list_tools",
            description="List tools exposed by a connected user-created remote MCP server. Because an MCP server is third-party code, this requires explicit approval.",
            parameters={"connector": {"type": "string", "description": "The custom MCP connector slug from custom_connector_list."}},
            required=["connector"],
            irreversible=True,
            runtime="connector",
            idempotency="read_only",
            approval_required=True,
            timeout_seconds=40,
            max_attempts=1,
        )

    async def execute(self, connector: str, **_: Any) -> dict[str, Any]:
        _definition, client = await _mcp_client_for(connector)
        try:
            tools = await client.list_tools()
            return {"tools": [{"name": str(tool.name), "description": str(tool.description or "")[:500], "input_schema": tool.inputSchema or {}} for tool in tools[:60]]}
        finally:
            await client.close()


class CustomMcpCallTool(BaseTool):
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="custom_mcp_call",
            description="Call one tool on a connected user-created remote MCP server after exact user confirmation. Do not assume a tool is safe; inspect its schema first with custom_mcp_list_tools.",
            parameters={
                "connector": {"type": "string", "description": "The custom MCP connector slug from custom_connector_list."},
                "tool_name": {"type": "string", "description": "Exact tool name returned by custom_mcp_list_tools."},
                "arguments": {"type": "object", "description": "Arguments matching the inspected MCP tool schema."},
            },
            required=["connector", "tool_name"],
            irreversible=True,
            runtime="connector",
            idempotency="non_idempotent_write",
            approval_required=True,
            timeout_seconds=60,
            max_attempts=1,
        )

    async def execute(self, connector: str, tool_name: str, arguments: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        _definition, client = await _mcp_client_for(connector)
        try:
            tools = await client.list_tools()
            allowed = {str(tool.name) for tool in tools}
            if tool_name not in allowed:
                raise ValueError("This MCP tool is not exposed by the connected server. Inspect its current tool list first.")
            result = await client.call_tool(tool_name, arguments or {})
            text = str(result)
            return {"tool_name": tool_name, "result": text[:20_000], "truncated": len(text) > 20_000}
        finally:
            await client.close()