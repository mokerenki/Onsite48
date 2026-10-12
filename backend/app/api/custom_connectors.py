"""Self-service custom API and remote MCP connector management.

This endpoint family persists only user-owned remote connector definitions. It
never accepts local commands, private-network targets, browser credentials, or
secret values in URL parameters. Secrets are encrypted before persistence and
are never returned by a management response.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.connectors import membership_for_user
from app.core.security import get_current_user, get_db_session
from app.mcp_clients.base_mcp_client import MCPClient
from app.models import CustomConnector, User
from app.services.agent_computer import UnsafeBrowserTarget, validate_public_url
from app.services.connector_vault import decrypt_credentials, encrypt_credentials

router = APIRouter(prefix="/api/custom-connectors", tags=["custom-connectors"])

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
_ALLOWED_AUTH_MODES = {"none", "bearer", "api_key_header", "api_key_query", "custom_header"}


class CustomApiCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    base_url: str = Field(min_length=12, max_length=2048)
    health_path: str | None = Field(default=None, max_length=1024)
    description: str | None = Field(default=None, max_length=2000)
    usage_guide: str | None = Field(default=None, max_length=6000)
    auth_mode: Literal["none", "bearer", "api_key_header", "api_key_query", "custom_header"] = "none"
    credential_name: str | None = Field(default=None, max_length=120)
    secret: str | None = None


class McpHeaderInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    value: str


class CustomMcpCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    endpoint: str = Field(min_length=12, max_length=2048)
    description: str | None = Field(default=None, max_length=2000)
    usage_guide: str | None = Field(default=None, max_length=6000)
    headers: list[McpHeaderInput] = Field(default_factory=list, max_length=20)


class ImportMcpRequest(BaseModel):
    config_json: str = Field(min_length=2, max_length=20_000)


class CustomConnectorUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    usage_guide: str | None = Field(default=None, max_length=6000)
    health_path: str | None = Field(default=None, max_length=1024)
    secret: str | None = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def slugify(value: str) -> str:
    slug = _SLUG_PATTERN.sub("-", value.lower()).strip("-")[:72]
    if not slug:
        raise ValueError("Enter a connector name containing letters or numbers.")
    return slug


def _header_name(value: str) -> str:
    name = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9-]{1,120}", name):
        raise ValueError("Credential and header names may use only letters, numbers, and hyphens.")
    return name


def _secret(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 8192:
        # This deliberately does not repeat the invalid input in the error.
        raise ValueError("The connector secret must be between 1 and 8192 characters.")
    return value


def _https_public_url(value: str) -> str:
    safe = validate_public_url(value)
    parsed = urlparse(safe)
    if parsed.scheme != "https":
        raise ValueError("Custom connectors require an HTTPS remote endpoint.")
    if parsed.username or parsed.password:
        raise ValueError("Put credentials in the encrypted connector fields, not in the URL.")
    return safe.rstrip("/")


async def _confirm_resolved_public_hostname(url: str) -> None:
    """Reject endpoints whose current DNS answers are local/private addresses."""
    hostname = urlparse(url).hostname
    if not hostname:
        raise ValueError("A complete HTTPS endpoint is required.")
    try:
        addresses = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM),
        )
    except OSError as exc:
        raise ValueError("Synth could not resolve the connector hostname.") from exc
    if not addresses:
        raise ValueError("Synth could not resolve the connector hostname.")
    for _family, _type, _proto, _canonname, sockaddr in addresses:
        address = ipaddress.ip_address(sockaddr[0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified:
            raise UnsafeBrowserTarget("Private, local, and internal network addresses are not permitted for custom connectors.")


def _relative_path(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    candidate = value.strip()
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc or candidate.startswith("//") or ".." in parsed.path.split("/"):
        raise ValueError("Health paths must be relative to the registered HTTPS endpoint.")
    return candidate if candidate.startswith("/") else f"/{candidate}"


def _endpoint(base_url: str, relative_path: str | None) -> str:
    if not relative_path:
        return base_url
    parsed_relative = urlparse(relative_path)
    if parsed_relative.scheme or parsed_relative.netloc or relative_path.startswith("//") or ".." in parsed_relative.path.split("/"):
        raise ValueError("Connector paths must remain relative to the registered HTTPS origin.")
    target = urljoin(f"{base_url}/", relative_path.lstrip("/"))
    if urlparse(target).netloc != urlparse(base_url).netloc or urlparse(target).scheme != "https":
        raise ValueError("Connector paths may not leave the registered HTTPS origin.")
    return target


def _connector_summary(connector: CustomConnector, *, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    config = connector.configuration or {}
    return {
        "id": str(connector.id),
        "name": connector.name,
        "slug": connector.slug,
        "kind": connector.kind,
        "base_url": connector.base_url,
        "health_path": connector.health_path,
        "description": connector.description,
        "usage_guide": connector.usage_guide,
        "auth_mode": config.get("auth_mode", "none"),
        "credential_name": config.get("credential_name"),
        "header_names": config.get("header_names", []),
        "has_credentials": bool(connector.encrypted_credentials),
        "status": connector.status,
        "last_tested_at": connector.last_tested_at.isoformat() if connector.last_tested_at else None,
        "last_error": connector.last_error,
        "tools": tools,
        "created_at": connector.created_at.isoformat(),
        "updated_at": connector.updated_at.isoformat(),
    }


async def _owned_connector(session: AsyncSession, user: User, connector_id: str) -> CustomConnector:
    workspace = await membership_for_user(session, user)
    connector = await session.scalar(select(CustomConnector).where(
        CustomConnector.id == connector_id,
        CustomConnector.organization_id == workspace.organization_id,
        CustomConnector.user_id == user.id,
    ))
    if connector is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom connector not found.")
    return connector


def _api_credentials(connector: CustomConnector) -> tuple[dict[str, str], dict[str, str]]:
    config = connector.configuration or {}
    mode = config.get("auth_mode", "none")
    if mode not in _ALLOWED_AUTH_MODES:
        raise RuntimeError("This custom API connector has an unsupported authentication configuration.")
    credentials = decrypt_credentials(connector.encrypted_credentials) or {}
    secret = credentials.get("secret")
    header_name = str(config.get("credential_name") or "")
    headers: dict[str, str] = {"Accept": "application/json"}
    params: dict[str, str] = {}
    if mode == "none":
        return headers, params
    if not secret:
        raise RuntimeError("This custom API connector needs its secret to be entered again.")
    if mode == "bearer":
        headers["Authorization"] = f"Bearer {secret}"
    elif mode in {"api_key_header", "custom_header"}:
        headers[_header_name(header_name or "X-Api-Key")] = str(secret)
    elif mode == "api_key_query":
        params[_header_name(header_name or "api_key")] = str(secret)
    return headers, params


async def _test_api(connector: CustomConnector) -> dict[str, Any]:
    url = _endpoint(connector.base_url, connector.health_path)
    await _confirm_resolved_public_hostname(url)
    headers, params = _api_credentials(connector)
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        response = await client.get(url, headers=headers, params=params)
    if response.status_code >= 400:
        raise RuntimeError(f"The health endpoint returned HTTP {response.status_code}.")
    return {"status_code": response.status_code, "endpoint": url}


async def _test_mcp(connector: CustomConnector) -> list[dict[str, Any]]:
    await _confirm_resolved_public_hostname(connector.base_url)
    credentials = decrypt_credentials(connector.encrypted_credentials) or {}
    headers = credentials.get("headers") if isinstance(credentials.get("headers"), dict) else {}
    client = MCPClient(name=connector.slug, url=connector.base_url, headers={str(key): str(value) for key, value in headers.items()})
    try:
        if not await client.connect():
            raise RuntimeError("Synth could not initialize the remote MCP server.")
        tools = await client.list_tools()
        if not tools:
            raise RuntimeError("The remote MCP server did not expose any tools over its SSE endpoint.")
        return [
            {"name": str(tool.name)[:160], "description": str(tool.description or "")[:500], "input_schema": tool.inputSchema or {}}
            for tool in tools[:60]
        ]
    finally:
        await client.close()


@router.get("")
async def list_custom_connectors(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    workspace = await membership_for_user(session, user)
    connectors = list((await session.scalars(select(CustomConnector).where(
        CustomConnector.organization_id == workspace.organization_id,
        CustomConnector.user_id == user.id,
    ).order_by(CustomConnector.name.asc()))).all())
    return {"connectors": [_connector_summary(connector) for connector in connectors]}


@router.post("/apis", status_code=status.HTTP_201_CREATED)
async def create_custom_api(
    payload: CustomApiCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        base_url = _https_public_url(payload.base_url)
        health_path = _relative_path(payload.health_path)
        secret = _secret(payload.secret)
        credential_name = _header_name(payload.credential_name) if payload.credential_name else None
        if payload.auth_mode != "none" and not secret:
            raise ValueError("Enter a secret for the selected API authentication method.")
        if payload.auth_mode in {"api_key_header", "api_key_query", "custom_header"} and not credential_name:
            raise ValueError("Enter the header or query parameter name used by this API.")
    except (UnsafeBrowserTarget, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    workspace = await membership_for_user(session, user)
    connector = CustomConnector(
        organization_id=workspace.organization_id,
        user_id=user.id,
        name=payload.name.strip(),
        slug=slugify(payload.name),
        kind="api",
        base_url=base_url,
        health_path=health_path,
        description=(payload.description or "").strip() or None,
        usage_guide=(payload.usage_guide or "").strip() or None,
        configuration={"auth_mode": payload.auth_mode, "credential_name": credential_name},
        encrypted_credentials=encrypt_credentials({"secret": secret}) if secret else None,
    )
    session.add(connector)
    try:
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A custom connector with that name already exists in this workspace.") from exc
    await session.refresh(connector)
    return _connector_summary(connector)


@router.post("/mcps", status_code=status.HTTP_201_CREATED)
async def create_custom_mcp(
    payload: CustomMcpCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        endpoint = _https_public_url(payload.endpoint)
        headers: dict[str, str] = {}
        for item in payload.headers:
            name = _header_name(item.name)
            secret = _secret(item.value)
            assert secret is not None
            headers[name] = secret
    except (UnsafeBrowserTarget, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    workspace = await membership_for_user(session, user)
    connector = CustomConnector(
        organization_id=workspace.organization_id,
        user_id=user.id,
        name=payload.name.strip(),
        slug=slugify(payload.name),
        kind="mcp",
        base_url=endpoint,
        description=(payload.description or "").strip() or None,
        usage_guide=(payload.usage_guide or "").strip() or None,
        configuration={"transport": "sse", "header_names": sorted(headers)},
        encrypted_credentials=encrypt_credentials({"headers": headers}) if headers else None,
    )
    session.add(connector)
    try:
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A custom connector with that name already exists in this workspace.") from exc
    await session.refresh(connector)
    return _connector_summary(connector)


@router.post("/mcps/import", status_code=status.HTTP_201_CREATED)
async def import_custom_mcp(
    payload: ImportMcpRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        document = json.loads(payload.config_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The MCP JSON is invalid.") from exc
    servers = document.get("mcpServers") if isinstance(document, dict) else None
    if not isinstance(servers, dict) or len(servers) != 1:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Import exactly one remote MCP server in an mcpServers object.")
    name, config = next(iter(servers.items()))
    if not isinstance(name, str) or not isinstance(config, dict) or not isinstance(config.get("url"), str):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The imported MCP requires a name and an HTTPS SSE url.")
    raw_headers = config.get("headers") or {}
    if not isinstance(raw_headers, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in raw_headers.items()):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Imported MCP headers must be a string-to-string object.")
    request = CustomMcpCreateRequest(
        name=name,
        endpoint=config["url"],
        description=str(config.get("description") or "")[:2000] or None,
        usage_guide=str(config.get("note") or "")[:6000] or None,
        headers=[McpHeaderInput(name=key, value=value) for key, value in raw_headers.items()],
    )
    return await create_custom_mcp(request, user, session)


@router.post("/{connector_id}/test")
async def test_custom_connector(
    connector_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    connector = await _owned_connector(session, user, connector_id)
    try:
        if connector.kind == "api":
            outcome: dict[str, Any] = {"test": await _test_api(connector)}
        elif connector.kind == "mcp":
            outcome = {"tools": await _test_mcp(connector)}
        else:
            raise RuntimeError("Unknown custom connector type.")
        connector.status = "connected"
        connector.last_error = None
        connector.last_tested_at = utcnow()
        await session.commit()
        return {"ok": True, **_connector_summary(connector, tools=outcome.get("tools")), **outcome}
    except Exception as exc:
        connector.status = "error"
        connector.last_error = str(exc).replace("\n", " ")[:1000]
        connector.last_tested_at = utcnow()
        await session.commit()
        return {"ok": False, "detail": "The connector test did not complete. Check the endpoint, supported transport, and credentials.", **_connector_summary(connector)}


@router.patch("/{connector_id}")
async def update_custom_connector(
    connector_id: str,
    payload: CustomConnectorUpdateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    connector = await _owned_connector(session, user, connector_id)
    try:
        if payload.name is not None:
            connector.name = payload.name.strip()
            connector.slug = slugify(payload.name)
        if payload.description is not None:
            connector.description = payload.description.strip() or None
        if payload.usage_guide is not None:
            connector.usage_guide = payload.usage_guide.strip() or None
        if payload.health_path is not None:
            if connector.kind != "api":
                raise ValueError("Only custom API connectors have a health path.")
            connector.health_path = _relative_path(payload.health_path)
        if payload.secret is not None:
            secret = _secret(payload.secret)
            current = decrypt_credentials(connector.encrypted_credentials) or {}
            if connector.kind == "api":
                connector.encrypted_credentials = encrypt_credentials({"secret": secret})
            else:
                raise ValueError("Replace custom MCP headers by importing or recreating the connector.")
        connector.status = "configured"
        connector.last_error = None
        await session.commit()
    except (UnsafeBrowserTarget, ValueError) as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A custom connector with that name already exists in this workspace.") from exc
    await session.refresh(connector)
    return _connector_summary(connector)


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_custom_connector(
    connector_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    connector = await _owned_connector(session, user, connector_id)
    await session.delete(connector)
    await session.commit()