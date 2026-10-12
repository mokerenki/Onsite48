"""Authenticated connector marketplace and credential lifecycle API."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional
from urllib.parse import urlencode

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import CONFIG
from app.core.security import get_current_user, get_db_session
from app.models import (
    ConnectorAuthType,
    ConnectorConnection,
    ConnectorStatus,
    OAuthAuthorizationState,
    OrganizationMember,
    User,
)
from app.services.connector_vault import encrypt_credentials
from app.connectors_sdk import marketplace_manifests

logger = structlog.get_logger("onsite48.api.connectors")
router = APIRouter(prefix="/api/connectors", tags=["connectors"])


class ConnectorDefinition(BaseModel):
    id: str
    name: str
    description: str
    category: str
    auth_type: ConnectorAuthType
    enabled: bool
    required_environment: list[str]


class ConnectionResponse(BaseModel):
    connector_id: str
    status: ConnectorStatus
    auth_type: ConnectorAuthType
    account_name: Optional[str] = None
    scopes: list[str]
    expires_at: Optional[datetime] = None
    last_error: Optional[str] = None


class ConnectorCatalogItem(ConnectorDefinition):
    connection: ConnectionResponse


class ApiKeyConnectRequest(BaseModel):
    api_key: str = Field(min_length=4, max_length=8192)
    account_name: Optional[str] = Field(default=None, max_length=255)


CONNECTOR_CATALOG: dict[str, dict[str, Any]] = {
    "github": {
        "name": "GitHub",
        "description": "Read repositories, create issues, and automate software delivery workflows.",
        "category": "development",
        "auth_type": ConnectorAuthType.OAUTH2,
        "client_id": "GITHUB_CLIENT_ID",
        "client_secret": "GITHUB_CLIENT_SECRET",
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "profile_url": "https://api.github.com/user",
        "scopes": ["read:user", "repo", "workflow"],
    },
    "gmail": {
        "name": "Gmail",
        "description": "Search, summarize, draft, and send email with explicit approval for external writes.",
        "category": "productivity",
        "auth_type": ConnectorAuthType.OAUTH2,
        "client_id": "GOOGLE_CLIENT_ID",
        "client_secret": "GOOGLE_CLIENT_SECRET",
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "profile_url": "https://openidconnect.googleapis.com/v1/userinfo",
        "scopes": [
            "openid",
            "email",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.compose",
            "https://www.googleapis.com/auth/gmail.send",
        ],
    },
    "google_calendar": {
        "name": "Google Calendar",
        "description": "List calendar events and create approval-gated calendar events.",
        "category": "productivity",
        "auth_type": ConnectorAuthType.OAUTH2,
        "client_id": "GOOGLE_CLIENT_ID",
        "client_secret": "GOOGLE_CLIENT_SECRET",
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "profile_url": "https://openidconnect.googleapis.com/v1/userinfo",
        "scopes": ["openid", "email", "https://www.googleapis.com/auth/calendar"],
    },
    "slack": {
        "name": "Slack",
        "description": "Find workspace knowledge and draft or send team messages with user confirmation.",
        "category": "productivity",
        "auth_type": ConnectorAuthType.OAUTH2,
        "client_id": "SLACK_CLIENT_ID",
        "client_secret": "SLACK_CLIENT_SECRET",
        "authorize_url": "https://slack.com/oauth/v2/authorize",
        "token_url": "https://slack.com/api/oauth.v2.access",
        "profile_url": "https://slack.com/api/auth.test",
        "scopes": ["channels:read", "channels:history", "chat:write"],
    },
    "outlook": {
        "name": "Microsoft Outlook",
        "description": "Connect Microsoft 365 mail and calendar data for proactive executive workflows.",
        "category": "productivity",
        "auth_type": ConnectorAuthType.OAUTH2,
        "client_id": "OUTLOOK_CLIENT_ID",
        "client_secret": "OUTLOOK_CLIENT_SECRET",
        "authorize_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "profile_url": "https://graph.microsoft.com/v1.0/me",
        "scopes": ["offline_access", "User.Read", "Mail.Read", "Mail.Send", "Calendars.Read"],
    },
    "salesforce": {
        "name": "Salesforce",
        "description": "Research accounts, enrich CRM records, and prepare opportunity briefs.",
        "category": "sales",
        "auth_type": ConnectorAuthType.OAUTH2,
        "client_id": "SALESFORCE_CLIENT_ID",
        "client_secret": "SALESFORCE_CLIENT_SECRET",
        "authorize_url": "https://login.salesforce.com/services/oauth2/authorize",
        "token_url": "https://login.salesforce.com/services/oauth2/token",
        "profile_url": "https://login.salesforce.com/services/oauth2/userinfo",
        "scopes": ["api", "refresh_token"],
    },
    "atlassian": {
        "name": "Atlassian (Rovo)",
        "description": "Connect Atlassian Cloud to search Jira issues, read Confluence content, and create approved work items.",
        "category": "productivity",
        "auth_type": ConnectorAuthType.OAUTH2,
        "client_id": "ATLASSIAN_CLIENT_ID",
        "client_secret": "ATLASSIAN_CLIENT_SECRET",
        "authorize_url": "https://auth.atlassian.com/authorize",
        "token_url": "https://auth.atlassian.com/oauth/token",
        "profile_url": "https://api.atlassian.com/me",
        "scopes": ["read:jira-user", "read:confluence-content.all", "write:jira-work"],
    },
    "brand24": {
        "name": "Brand24",
        "description": "Monitor brand mentions, sentiment, and competitor signals.",
        "category": "marketing",
        "auth_type": ConnectorAuthType.API_KEY,
        "scopes": [],
    },
    "elevenlabs": {
        "name": "ElevenLabs",
        "description": "Generate approved voice assets for marketing workflows.",
        "category": "ai",
        "auth_type": ConnectorAuthType.API_KEY,
        "scopes": [],
    },
    "similarweb": {
        "name": "Similarweb",
        "description": "Retrieve website traffic, audience, and acquisition intelligence.",
        "category": "analytics",
        "auth_type": ConnectorAuthType.API_KEY,
        "scopes": [],
    },
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_redirect_uri(connector_id: str) -> str:
    base = os.getenv("OAUTH_REDIRECT_BASE_URL", f"{CONFIG.frontend_url}/api/connectors/oauth/callback").rstrip("/")
    return f"{base}/{connector_id}"


def config_is_available(config: dict[str, Any]) -> bool:
    if config["auth_type"] == ConnectorAuthType.API_KEY:
        return True
    return bool(os.getenv(config["client_id"]) and os.getenv(config["client_secret"]))


async def membership_for_user(session: AsyncSession, user: User) -> OrganizationMember:
    membership = await session.scalar(
        select(OrganizationMember).where(OrganizationMember.user_id == user.id).order_by(OrganizationMember.created_at.asc())
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No workspace is associated with this account.")
    return membership


async def find_connection(
    session: AsyncSession,
    user: User,
    membership: OrganizationMember,
    connector_id: str,
) -> Optional[ConnectorConnection]:
    return await session.scalar(
        select(ConnectorConnection).where(
            ConnectorConnection.organization_id == membership.organization_id,
            ConnectorConnection.user_id == user.id,
            ConnectorConnection.connector_id == connector_id,
        )
    )


def as_connection_response(connector_id: str, auth_type: ConnectorAuthType, connection: Optional[ConnectorConnection]) -> ConnectionResponse:
    if connection is None:
        return ConnectionResponse(connector_id=connector_id, auth_type=auth_type, status=ConnectorStatus.DISCONNECTED, scopes=[])
    visible_status = connection.status
    if visible_status == ConnectorStatus.CONNECTED and connection.expires_at and connection.expires_at <= utcnow():
        visible_status = ConnectorStatus.EXPIRED
    return ConnectionResponse(
        connector_id=connector_id,
        auth_type=auth_type,
        status=visible_status,
        account_name=connection.external_account_name,
        scopes=connection.scopes or [],
        expires_at=connection.expires_at,
        last_error=connection.last_error,
    )


def connector_health(config: dict[str, Any], connection: Optional[ConnectorConnection], *, now: datetime | None = None) -> dict[str, Any]:
    """Return safe, actionable account health without exposing credentials."""
    observed_at = now or utcnow()
    configured = config_is_available(config)
    if connection is None or connection.status == ConnectorStatus.DISCONNECTED:
        state, action = "not_connected", "connect"
    elif connection.status == ConnectorStatus.ERROR:
        state, action = "error", "reconnect"
    elif connection.expires_at and connection.expires_at <= observed_at:
        state, action = "expired", "reconnect"
    elif connection.expires_at and connection.expires_at <= observed_at + timedelta(minutes=15):
        state, action = "expiring_soon", "reconnect"
    else:
        state, action = "connected", "none"
    return {
        "connector_id": str(config.get("id") or ""),
        "state": state,
        "action": action,
        "configured": configured,
        "account_name": getattr(connection, "external_account_name", None) if connection else None,
        "scopes": list(getattr(connection, "scopes", None) or []) if connection else [],
        "expires_at": connection.expires_at if connection else None,
        "last_error": connection.last_error if connection else None,
        "reconnect_url": None if config.get("auth_type") == ConnectorAuthType.API_KEY else "oauth",
    }


@router.get("/marketplace")
async def marketplace(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    membership = await membership_for_user(session, user)
    items: list[dict[str, Any]] = []
    for manifest in marketplace_manifests():
        runtime = CONNECTOR_CATALOG.get(manifest["id"])
        connection = await find_connection(session, user, membership, manifest["id"]) if runtime else None
        items.append({
            **manifest,
            "configured": config_is_available(runtime) if runtime else False,
            "redirect_uri": get_redirect_uri(manifest["id"]) if manifest["auth_type"] == "oauth2" and runtime else None,
            "connection": as_connection_response(manifest["id"], runtime["auth_type"], connection).model_dump(mode="json") if runtime else None,
        })
    return items


@router.get("", response_model=list[ConnectorCatalogItem])
async def list_connectors(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[ConnectorCatalogItem]:
    membership = await membership_for_user(session, user)
    items: list[ConnectorCatalogItem] = []
    for connector_id, config in CONNECTOR_CATALOG.items():
        connection = await find_connection(session, user, membership, connector_id)
        env_names = [name for name in [config.get("client_id"), config.get("client_secret")] if name]
        items.append(
            ConnectorCatalogItem(
                id=connector_id,
                name=config["name"],
                description=config["description"],
                category=config["category"],
                auth_type=config["auth_type"],
                enabled=config_is_available(config),
                required_environment=env_names,
                connection=as_connection_response(connector_id, config["auth_type"], connection),
            )
        )
    return items


@router.get("/{connector_id}/health")
async def connector_health_status(
    connector_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    config = CONNECTOR_CATALOG.get(connector_id)
    if config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown connector.")
    membership = await membership_for_user(session, user)
    connection = await find_connection(session, user, membership, connector_id)
    return connector_health({**config, "id": connector_id}, connection)


@router.get("/{connector_id}/oauth/start")
async def start_oauth_connection(
    connector_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    config = CONNECTOR_CATALOG.get(connector_id)
    if config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown connector.")
    if config["auth_type"] != ConnectorAuthType.OAUTH2:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This connector uses an API key rather than OAuth.")
    if not config_is_available(config):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="This connector has not been configured by the Onsite48 operator.")
    membership = await membership_for_user(session, user)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    redirect_uri = get_redirect_uri(connector_id)
    session.add(
        OAuthAuthorizationState(
            state=state,
            connector_id=connector_id,
            user_id=user.id,
            organization_id=membership.organization_id,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
            expires_at=utcnow() + timedelta(minutes=10),
        )
    )
    query = {
        "client_id": os.getenv(config["client_id"]),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        "scope": " ".join(config["scopes"]),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    # Google must request offline access or no refresh_token is stored and
    # Calendar/Gmail connections expire within ~1 hour with no renew path.
    if connector_id in {"gmail", "google_calendar"} or str(config.get("authorize_url") or "").startswith(
        "https://accounts.google.com"
    ):
        query.update({"access_type": "offline", "prompt": "consent"})
    if connector_id == "atlassian":
        query.update({"audience": "api.atlassian.com", "prompt": "consent"})
    return {"authorization_url": f"{config['authorize_url']}?{urlencode(query)}"}


async def exchange_code(config: dict[str, Any], code: str, state: OAuthAuthorizationState) -> dict[str, Any]:
    token_payload = {
        "client_id": os.getenv(config["client_id"]),
        "client_secret": os.getenv(config["client_secret"]),
        "code": code,
        "redirect_uri": state.redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": state.code_verifier,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(config["token_url"], data=token_payload, headers={"Accept": "application/json"})
        response.raise_for_status()
        payload = response.json()
        if config["token_url"].startswith("https://slack.com") and not payload.get("ok", True):
            raise httpx.HTTPStatusError(payload.get("error", "Slack OAuth failed"), request=response.request, response=response)
        return payload


async def resolve_external_account(config: dict[str, Any], credentials: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    if not config.get("profile_url") or not credentials.get("access_token"):
        return None, None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(config["profile_url"], headers={"Authorization": f"Bearer {credentials['access_token']}", "Accept": "application/json"})
            response.raise_for_status()
            profile = response.json()
            if "user" in profile and isinstance(profile["user"], dict):
                profile = profile["user"]
            account_id = str(profile.get("id") or profile.get("sub") or profile.get("user_id") or "") or None
            account_name = profile.get("name") or profile.get("login") or profile.get("email") or profile.get("user")
            return account_id, str(account_name)[:255] if account_name else None
    except httpx.HTTPError:
        return None, None


@router.get("/oauth/callback/{connector_id}", response_class=HTMLResponse)
async def complete_oauth_connection(
    connector_id: str,
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    config = CONNECTOR_CATALOG.get(connector_id)
    if config is None or config["auth_type"] != ConnectorAuthType.OAUTH2:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown OAuth connector.")
    authorization = await session.scalar(select(OAuthAuthorizationState).where(OAuthAuthorizationState.state == state))
    if authorization is None or authorization.connector_id != connector_id or authorization.expires_at <= utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The connector authorization request expired or is invalid.")
    await session.delete(authorization)
    try:
        credentials = await exchange_code(config, code, authorization)
        account_id, account_name = await resolve_external_account(config, credentials)
    except httpx.HTTPError as exc:
        logger.warning("connector.oauth_exchange_failed", connector_id=connector_id, error=str(exc))
        return HTMLResponse("<script>window.opener?.postMessage({type:'connector_error'}, window.location.origin);window.close()</script><p>Connection failed. You may close this window.</p>", status_code=502)
    connection = await session.scalar(
        select(ConnectorConnection).where(
            ConnectorConnection.organization_id == authorization.organization_id,
            ConnectorConnection.user_id == authorization.user_id,
            ConnectorConnection.connector_id == connector_id,
        )
    )
    expires_at = utcnow() + timedelta(seconds=int(credentials.get("expires_in", 3600))) if credentials.get("expires_in") else None
    if connection is None:
        connection = ConnectorConnection(
            organization_id=authorization.organization_id,
            user_id=authorization.user_id,
            connector_id=connector_id,
            auth_type=ConnectorAuthType.OAUTH2,
        )
        session.add(connection)
    connection.status = ConnectorStatus.CONNECTED
    connection.encrypted_credentials = encrypt_credentials(credentials)
    connection.external_account_id = account_id
    connection.external_account_name = account_name
    connection.scopes = config["scopes"]
    connection.expires_at = expires_at
    connection.last_error = None
    return HTMLResponse(
        f"<script>window.opener?.postMessage({{type:'connector_success',connector:'{connector_id}'}}, window.location.origin);window.close()</script><p>{config['name']} is connected. You may close this window.</p>"
    )


@router.post("/{connector_id}/api-key", response_model=ConnectionResponse)
async def connect_api_key(
    connector_id: str,
    payload: ApiKeyConnectRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ConnectionResponse:
    config = CONNECTOR_CATALOG.get(connector_id)
    if config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown connector.")
    if config["auth_type"] != ConnectorAuthType.API_KEY:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This connector requires OAuth rather than an API key.")
    membership = await membership_for_user(session, user)
    connection = await find_connection(session, user, membership, connector_id)
    if connection is None:
        connection = ConnectorConnection(
            organization_id=membership.organization_id,
            user_id=user.id,
            connector_id=connector_id,
            auth_type=ConnectorAuthType.API_KEY,
        )
        session.add(connection)
    connection.status = ConnectorStatus.CONNECTED
    connection.encrypted_credentials = encrypt_credentials({"api_key": payload.api_key})
    connection.external_account_name = payload.account_name
    connection.scopes = []
    connection.expires_at = None
    connection.last_error = None
    await session.flush()
    return as_connection_response(connector_id, ConnectorAuthType.API_KEY, connection)


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_connector(
    connector_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    config = CONNECTOR_CATALOG.get(connector_id)
    if config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown connector.")
    membership = await membership_for_user(session, user)
    connection = await find_connection(session, user, membership, connector_id)
    if connection:
        connection.status = ConnectorStatus.DISCONNECTED
        connection.encrypted_credentials = None
        connection.expires_at = None
        connection.last_error = None