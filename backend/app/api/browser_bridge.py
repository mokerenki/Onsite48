"""APIs for the consented local-browser Onsite48 Bridge extension."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user, get_db_session
from app.models import BrowserBridgeCommand, BrowserBridgeDevice, BrowserBridgeTrustRule, OperationEvent, OrganizationMember, TaskContext, User
from app.services.bridge_transport import bridge_transport_registry
from app.services.browser_live_registry import browser_live_registry
from app.services.browser_bridge import extension_command_payload

router = APIRouter(prefix="/api/bridge", tags=["onsite48-bridge"])

# The extension authenticates with the first frame after the handshake.
# A socket that never authenticates is closed rather than held open.
BRIDGE_AUTH_TIMEOUT_SECONDS = 30


class PairingStartRequest(BaseModel):
    display_name: str = Field(default="My browser", min_length=1, max_length=160)


class DevicePairStartRequest(BaseModel):
    """Extension-initiated device pair (no paste-token default path)."""
    display_name: str = Field(default="Chrome · Onsite48 Browser", min_length=1, max_length=160)


class DevicePairCompleteRequest(BaseModel):
    device_id: str = Field(min_length=8, max_length=128)
    poll_token: str = Field(min_length=8, max_length=256)


class DevicePairConfirmRequest(BaseModel):
    """Signed-in product UI confirms the extension's Connect code."""
    user_code: str = Field(min_length=4, max_length=16)


class DeviceRefreshRequest(BaseModel):
    device_credential: str = Field(min_length=16, max_length=512)


class ExtensionPairRequest(BaseModel):
    device_id: str = Field(min_length=12, max_length=128)
    pairing_code: str = Field(min_length=12, max_length=256)
    extension_name: str = Field(default="Onsite48 Bridge", min_length=1, max_length=160)
    # The current operator extension advertises fifteen granular capabilities.
    # BrowserBridgeService.ALLOWED_CAPABILITIES remains the authoritative gate.
    requested_capabilities: list[str] = Field(default_factory=list, max_length=20)


class CommandPreflightRequest(BaseModel):
    ready: bool
    origin: str | None = Field(default=None, max_length=2048)
    reason: str | None = Field(default=None, max_length=512)
    failure_code: str | None = Field(default=None, max_length=80)


class CommandCompletionRequest(BaseModel):
    result: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    verification: dict[str, Any] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=1000)
    lease_token: str | None = Field(default=None, min_length=12, max_length=128)
    cancelled: bool = False
    cancel_reason: str | None = Field(default=None, max_length=1000)


class CommandCancelRequest(BaseModel):
    reason: str = Field(default="The local browser operator was stopped by the user.", min_length=1, max_length=1000)


class TaskContextRequest(BaseModel):
    device_id: str = Field(min_length=12, max_length=128)
    thread_id: uuid.UUID | None = None
    task_goal: str | None = None
    planned_steps: list[str] = Field(default_factory=list)
    completed_steps: list[str] = Field(default_factory=list)
    current_step: str | None = None
    tab_history: list[dict[str, Any]] = Field(default_factory=list)
    active_tab_id: int | None = None
    locked_tab_id: int | None = None
    session_start_at: str | None = None
    status: str = Field(default="running")


class OperationEventRequest(BaseModel):
    device_id: str = Field(min_length=12, max_length=128)
    event_type: str = Field(min_length=1, max_length=64)
    event_data: dict[str, Any] = Field(default_factory=dict)
    thread_id: uuid.UUID | None = None


class SessionStateRequest(BaseModel):
    device_id: str = Field(min_length=12, max_length=128)
    session_state: str = Field(min_length=1, max_length=32)
    pause_reason: str | None = None


class RouteCommandRequest(BaseModel):
    device_id: str = Field(min_length=12, max_length=128)
    target_url: str = Field(min_length=8, max_length=2048)
    command_id: uuid.UUID | None = None


class WaitForChallengeRequest(BaseModel):
    device_id: str = Field(min_length=12, max_length=128)
    tab_id: int = Field(ge=0)
    timeout_seconds: int = Field(default=30, ge=1, le=120)


class OnBrowserEventRequest(BaseModel):
    device_id: str = Field(min_length=12, max_length=128)
    event_type: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    session_id: uuid.UUID | None = None


class TaskContextUpdateRequest(BaseModel):
    device_id: str = Field(min_length=12, max_length=128)
    thread_id: uuid.UUID | None = None
    task_goal: str | None = None
    planned_steps: list[str] | None = None
    completed_steps: list[str] | None = None
    current_step: str | None = None
    tab_history: list[dict[str, Any]] | None = None
    active_tab_id: int | None = None
    locked_tab_id: int | None = None
    status: str | None = None


class OperationEventData(BaseModel):
    event_type: str = Field(min_length=1, max_length=64)
    event_data: dict[str, Any] = Field(default_factory=dict)


class SessionStateUpdate(BaseModel):
    session_state: str = Field(min_length=1, max_length=32)
    pause_reason: str | None = None


class PollSessionStateRequest(BaseModel):
    device_id: str = Field(min_length=12, max_length=128)


class RouteCommandResult(BaseModel):
    tab_id: int
    url: str
    created: bool = False


class ChallengeResult(BaseModel):
    detected: bool
    challenge_type: str | None = None
    severity: str | None = None


class BrowserEventRequest(BaseModel):
    event_type: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    session_id: uuid.UUID | None = None


class TaskContextResponse(BaseModel):
    ok: bool


class OperationEventResponse(BaseModel):
    ok: bool


class SessionStateResponse(BaseModel):
    session_state: str
    pause_reason: str | None = None


class RouteCommandResponse(BaseModel):
    tab_id: int
    url: str
    created: bool


class WaitForChallengeResponse(BaseModel):
    detected: bool
    challenge_type: str | None = None


class OnBrowserEventResponse(BaseModel):
    ok: bool


class ActiveContextRequest(BaseModel):
    # URL is validated server-side and only its hostname is persisted.
    url: str = Field(default="https://selection.invalid", min_length=8, max_length=2048)
    title: str = Field(default="", max_length=240)
    tab_id: int | None = Field(default=None, ge=0)
    window_id: int | None = Field(default=None, ge=0)
    selected_at: str | None = Field(default=None, max_length=64)
    selection_state: str | None = Field(default="selected", max_length=80)


class AuthorizeRequest(BaseModel):
    origins: list[str] = Field(default_factory=list, max_length=50)
    capabilities: list[str] = Field(default_factory=list, max_length=20)


class TrustRuleRequest(BaseModel):
    device_id: uuid.UUID
    hostname: str = Field(min_length=1, max_length=255)
    allowed_capabilities: list[str] = Field(default_factory=list, max_length=8)
    enabled: bool = True


async def membership(session: AsyncSession, user: User) -> OrganizationMember:
    current = await session.scalar(
        select(OrganizationMember).where(OrganizationMember.user_id == user.id).order_by(OrganizationMember.created_at.asc())
    )
    if current is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No active workspace is associated with this account.")
    return current


def trust_rule_summary(rule: BrowserBridgeTrustRule) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "device_id": str(rule.device_id),
        "hostname": rule.hostname,
        "allowed_capabilities": rule.allowed_capabilities or [],
        "enabled": rule.enabled,
        "created_at": rule.created_at.isoformat(),
        "updated_at": rule.updated_at.isoformat(),
    }


def command_summary(command: BrowserBridgeCommand) -> dict[str, Any]:
    """Expose operational proof without returning page text, screenshots, or typed values."""
    result = command.result or {}
    verification = command.verification or {}
    return {
        "id": str(command.id),
        "command_type": command.command_type,
        "status": command.status,
        "requires_approval": command.requires_approval,
        "task_id": str(command.task_id) if command.task_id else None,
        "preflight_status": command.preflight_status,
        "preflight_origin": command.preflight_origin,
        "preflight_reason": command.preflight_reason,
        "hostname": str((command.payload or {}).get("expected_hostname") or ""),
        "target_tab": (command.payload or {}).get("target_tab") if isinstance((command.payload or {}).get("target_tab"), dict) else None,
        "url": str(result.get("url") or "")[:2048] or None,
        "challenge_detected": bool(result.get("challenge_detected")),
        "cancel_reason": command.cancel_reason,
        "error": command.error,
        "verification_status": verification.get("status") if isinstance(verification, dict) else None,
        "evidence_available": bool(command.evidence),
        "created_at": command.created_at.isoformat(),
        "claimed_at": command.claimed_at.isoformat() if command.claimed_at else None,
        "completed_at": command.completed_at.isoformat() if command.completed_at else None,
    }


def device_summary(device: BrowserBridgeDevice) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    seen = device.last_seen_at
    fresh = bool(seen and (now - seen.astimezone(timezone.utc)) <= timedelta(seconds=90))
    if device.revoked_at or device.status == "revoked":
        lifecycle = "revoked"
    elif device.status == "pending":
        lifecycle = "pending"
    elif device.status == "connected" and fresh:
        lifecycle = "connected"
    elif device.status == "connected":
        lifecycle = "offline"
    else:
        lifecycle = str(device.status or "offline")
    return {
        "id": str(device.id),
        "device_id": device.device_id,
        "display_name": device.display_name,
        "status": device.status,
        "lifecycle": lifecycle,
        "is_available": lifecycle == "connected",
        "granted_capabilities": device.granted_capabilities or [],
        "is_preferred": device.is_preferred,
        "active_context": device.active_context or {},
        "selected_tab": device.active_context or {},
        "session_state": device.session_state or "AUTHORIZING",
        "authorized_origins": json.loads(device.authorized_origins or "[]") if device.authorized_origins else [],
        "authorized_capabilities": json.loads(device.authorized_capabilities or "[]") if device.authorized_capabilities else [],
        "session_authorized_at": device.session_authorized_at.isoformat() if device.session_authorized_at else None,
        "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
        "paired_at": device.paired_at.isoformat() if device.paired_at else None,
        "revoked_at": device.revoked_at.isoformat() if device.revoked_at else None,
        "created_at": device.created_at.isoformat(),
        "last_seen_label": "just now" if fresh else ("not seen yet" if not seen else "offline"),
    }


@router.post("/pairings", status_code=status.HTTP_201_CREATED)
async def start_pairing(
    payload: PairingStartRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    current = await membership(session, user)
    device, pairing_code = await request.app.state.browser_bridge.create_pairing(
        organization_id=current.organization_id,
        user_id=user.id,
        display_name=payload.display_name,
    )
    return {
        "device": device_summary(device),
        "pairing_code": pairing_code,
        "expires_in_seconds": 600,
        "capability_notice": "The extension uses this one-time code to pair. It must never send browser cookies, saved passwords, or session tokens to Onsite48.",
    }


# ── Device credential path (default UX: install → Connect → authorize) ─────
# POST /api/bridge/device/pair/start  → device_id + user_code + poll_token
# POST /api/bridge/device/pair/complete → device_credential (refreshable)
# POST /api/bridge/device/refresh → short-lived bridge token from device identity

@router.post("/device/pair/start", status_code=status.HTTP_201_CREATED)
async def device_pair_start(
    payload: DevicePairStartRequest,
    request: Request,
):
    """Extension Connect: start an unauthenticated pending pair.

    Returns device_id, user_code, and poll_token. The signed-in user must confirm
    the user_code in Onsite48 (Settings → My Browser). The extension then finishes
    via unauthenticated /device/pair/complete using the poll_token.
    """
    bridge = request.app.state.browser_bridge
    return bridge.start_device_pair(display_name=payload.display_name)


@router.post("/device/pair/confirm")
async def device_pair_confirm(
    payload: DevicePairConfirmRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Product UI: bind a pending Connect code to the signed-in user.

    Call this from Settings → Connectors → My Browser after the extension shows
    a short user_code. The extension's poll of /device/pair/complete will then
    receive bridge_token.
    """
    current = await membership(session, user)
    bridge = request.app.state.browser_bridge
    return await bridge.confirm_device_pair(
        user_code=payload.user_code,
        organization_id=current.organization_id,
        user_id=user.id,
    )


@router.post("/device/pair/complete")
async def device_pair_complete(
    payload: DevicePairCompleteRequest,
    request: Request,
):
    """Extension finishes Connect after the user confirmed in the product.

    Unauthenticated on purpose: the extension cannot send the web app session.
    Proof is possession of poll_token issued by /device/pair/start and confirmed
    via /device/pair/confirm.
    """
    bridge = request.app.state.browser_bridge
    result = await bridge.complete_device_pair(
        device_id=payload.device_id,
        poll_token=payload.poll_token,
        extension_name="Onsite48 Browser",
        requested_capabilities=[
            "observe", "navigate", "click", "type", "scroll",
            "screenshot", "download", "upload", "takeover", "workspace",
        ],
    )
    # Still waiting for Settings confirm
    if isinstance(result, dict) and result.get("status") == "awaiting_confirmation":
        return {
            "status": "awaiting_confirmation",
            "user_code": result.get("user_code"),
            "message": result.get("message"),
            "bridge_token": None,
            "device_credential": None,
        }

    device, bridge_token = result  # type: ignore[misc]
    user_email = None
    try:
        from app.models import User
        from sqlalchemy import select as sa_select
        async with request.app.state.browser_bridge.session_factory() as session:
            db_user = await session.scalar(sa_select(User).where(User.id == device.user_id))
            if db_user is not None:
                user_email = getattr(db_user, "email", None)
    except Exception:
        user_email = None
    return {
        "device": device_summary(device),
        "device_credential": bridge_token,
        "bridge_token": bridge_token,
        "user_email": user_email,
        "session_state": "ACTIVE",
        "status": "connected",
    }


@router.post("/device/refresh")
async def device_refresh(
    payload: DeviceRefreshRequest,
    request: Request,
):
    """Rotate a short-lived bridge token from a stored device_credential."""
    bridge = request.app.state.browser_bridge
    device = await bridge.authenticate_extension(payload.device_credential)
    # Re-issue same shape for now (token is already the credential); production may mint short JWT
    return {
        "bridge_token": payload.device_credential,
        "device": device_summary(device),
        "expires_in_seconds": 3600,
    }


@router.get("/devices")
async def list_devices(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    current = await membership(session, user)
    devices = list((await session.scalars(select(BrowserBridgeDevice).where(
        BrowserBridgeDevice.organization_id == current.organization_id,
        BrowserBridgeDevice.user_id == user.id,
    ).order_by(BrowserBridgeDevice.updated_at.desc()))).all())
    return {"devices": [device_summary(device) for device in devices]}


@router.get("/commands")
async def list_command_history(
    limit: int = Query(default=30, ge=1, le=100),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    current = await membership(session, user)
    commands = list((await session.scalars(
        select(BrowserBridgeCommand).where(
            BrowserBridgeCommand.organization_id == current.organization_id,
            BrowserBridgeCommand.user_id == user.id,
        ).order_by(BrowserBridgeCommand.created_at.desc()).limit(limit)
    )).all())
    return {"commands": [command_summary(command) for command in commands]}


@router.get("/trust-rules")
async def list_trust_rules(
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    current = await membership(session, user)
    rules = await request.app.state.browser_bridge.list_trust_rules(
        organization_id=current.organization_id, user_id=user.id
    )
    return {"rules": [trust_rule_summary(rule) for rule in rules]}


@router.put("/trust-rules")
async def save_trust_rule(
    payload: TrustRuleRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    current = await membership(session, user)
    try:
        rule = await request.app.state.browser_bridge.upsert_trust_rule(
            organization_id=current.organization_id,
            user_id=user.id,
            device_id=payload.device_id,
            hostname=payload.hostname,
            allowed_capabilities=payload.allowed_capabilities,
            enabled=payload.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return trust_rule_summary(rule)


@router.delete("/trust-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trust_rule(
    rule_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    current = await membership(session, user)
    await request.app.state.browser_bridge.delete_trust_rule(
        organization_id=current.organization_id, user_id=user.id, rule_id=rule_id
    )


@router.put("/devices/{device_id}/preferred")
async def set_preferred_device(
    device_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    current = await membership(session, user)
    device = await request.app.state.browser_bridge.set_preferred_device(
        organization_id=current.organization_id,
        user_id=user.id,
        device_id=device_id,
    )
    return {"device": device_summary(device)}


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_device(
    device_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    current = await membership(session, user)
    await request.app.state.browser_bridge.revoke_device(
        organization_id=current.organization_id,
        user_id=user.id,
        device_id=device_id,
    )


@router.post("/extension/pair")
async def pair_extension(payload: ExtensionPairRequest, request: Request):
    device, bridge_token = await request.app.state.browser_bridge.pair_extension(
        device_id=payload.device_id,
        pairing_code=payload.pairing_code,
        extension_name=payload.extension_name,
        requested_capabilities=payload.requested_capabilities,
    )
    return {
        "bridge_token": bridge_token,
        "device": device_summary(device),
        "security_notice": "Store this bridge token only in the browser extension's local storage. Do not expose it to web pages.",
    }


async def bridge_device(request: Request, x_synth_bridge_token: str = Header(default="")) -> BrowserBridgeDevice:
    return await request.app.state.browser_bridge.authenticate_extension(x_synth_bridge_token)


@router.get("/extension/status")
async def extension_status(device: BrowserBridgeDevice = Depends(bridge_device)):
    """Verify an extension token without exposing token material or page data."""
    return {
        "device_id": str(device.id),
        "status": device.status,
        "transport_authorized": True,
    }


def _bridge_socket_token(websocket: WebSocket) -> str:
    """Extract the extension token from Sec-WebSocket-Protocol, never the URL."""
    offered = [value.strip() for value in websocket.headers.get("sec-websocket-protocol", "").split(",") if value.strip()]
    if "onsite48-bridge.v1" not in offered:
        return ""
    return next((value for value in offered if value != "onsite48-bridge.v1"), "")


async def bridge_websocket_endpoint(websocket: WebSocket, bridge: Any, browser_sessions: Any | None = None) -> None:
    """Serve one paired extension with typed, device-scoped command transport.

    The extension authenticates with the first frame (``{type: "auth",
    token}``) rather than the handshake subprotocol, so the socket is
    accepted first and authenticated before any command is delivered. The
    database remains authoritative. This socket only shortens delivery and
    completion latency; an older or disconnected extension continues to use
    the existing polling endpoint without changing command semantics.
    """
    offered = [value.strip() for value in websocket.headers.get("sec-websocket-protocol", "").split(",") if value.strip()]
    await websocket.accept(subprotocol="onsite48-bridge.v1" if "onsite48-bridge.v1" in offered else None)
    device: Any | None = None
    try:
        # The handshake carries no secret. A token offered as a second
        # subprotocol is still honored for older extension builds; the
        # current extension sends its token in the first frame instead.
        # Until authentication succeeds the socket is anonymous and
        # receives nothing but the auth outcome.
        token = _bridge_socket_token(websocket)
        if not token:
            first = await asyncio.wait_for(websocket.receive_json(), timeout=BRIDGE_AUTH_TIMEOUT_SECONDS)
            if not isinstance(first, dict) or str(first.get("type") or "") != "auth":
                await websocket.send_json({"type": "auth_reject", "reason": "Authentication is required before any bridge event."})
                await websocket.close(code=4401, reason="Authentication is required.")
                return
            token = str(first.get("token") or "")
        try:
            device = await bridge.authenticate_extension(token)
        except HTTPException:
            await websocket.send_json({"type": "auth_reject", "reason": "A valid Onsite48 Bridge device token is required."})
            await websocket.close(code=4401, reason="A valid Onsite48 Bridge device token is required.")
            return
        await bridge_transport_registry.register(device.id, websocket)
        await websocket.send_json({"type": "auth_ack", "device_id": str(device.id), "transport": "websocket"})
        await websocket.send_json({"type": "bridge_ready", "device_id": str(device.id), "transport": "websocket"})
        # Reconnects must not miss commands queued while the socket was absent.
        # These remain pending until preflight atomically claims them, so a polling
        # extension and an upgraded socket extension stay compatible.
        for pending in await bridge.list_commands_for_extension(device):
            await websocket.send_json({"type": "command_queued", "command": extension_command_payload(pending)})
        while True:
            event = await websocket.receive_json()
            event_type = str(event.get("type") or "")
            if event_type == "keepalive":
                await websocket.send_json({"type": "keepalive_ack"})
                continue
            if event_type == "tab_state_diff":
                live_session_id = event.get("session_id")
                if browser_sessions is not None and live_session_id:
                    try:
                        live_session = await browser_sessions.ingest_live_event(
                            session_id=uuid.UUID(str(live_session_id)),
                            organization_id=device.organization_id,
                            user_id=device.user_id,
                            device_id=device.id,
                            event_type="browser_tab_state_diff",
                            payload=event,
                        )
                        if live_session is not None:
                            await browser_live_registry.broadcast(str(live_session.id), {"type": "browser_session_state", "session_id": str(live_session.id), "status": live_session.status, "current_url": live_session.current_url, "current_observation": live_session.current_observation or {}, "tab_ids": live_session.tab_ids or [], "primary_tab_id": live_session.primary_tab_id, "updated_at": live_session.updated_at.isoformat()})
                    except (TypeError, ValueError):
                        pass
                await websocket.send_json({
                    "type": "tab_state_diff_ack",
                    "tab_id": event.get("tab_id"),
                    "session_id": live_session_id,
                    "observed_at": (event.get("snapshot") or {}).get("observed_at"),
                })
                continue
            if event_type == "tab_lifecycle":
                live_session_id = event.get("session_id")
                if browser_sessions is not None and live_session_id:
                    try:
                        live_session = await browser_sessions.ingest_live_event(
                            session_id=uuid.UUID(str(live_session_id)),
                            organization_id=device.organization_id,
                            user_id=device.user_id,
                            device_id=device.id,
                            event_type="browser_tab_lifecycle",
                            payload=event,
                        )
                        if live_session is not None:
                            await browser_live_registry.broadcast(str(live_session.id), {"type": "browser_session_state", "session_id": str(live_session.id), "status": live_session.status, "current_url": live_session.current_url, "current_observation": live_session.current_observation or {}, "tab_ids": live_session.tab_ids or [], "primary_tab_id": live_session.primary_tab_id, "updated_at": live_session.updated_at.isoformat()})
                    except (TypeError, ValueError):
                        pass
                await websocket.send_json({"type": "tab_lifecycle_ack", "session_id": live_session_id, "event": event.get("event"), "tab_id": event.get("tab_id")})
                continue
            try:
                command_id = uuid.UUID(str(event.get("command_id") or ""))
            except (TypeError, ValueError):
                await websocket.send_json({"type": "bridge_error", "error_code": "command_id_required", "message": "A valid command identifier is required."})
                continue

            if event_type == "command_preflight":
                command = await bridge.preflight_command(
                    device=device,
                    command_id=command_id,
                    ready=bool(event.get("ready")),
                    origin=event.get("origin") if isinstance(event.get("origin"), str) else None,
                    reason=event.get("reason") if isinstance(event.get("reason"), str) else None,
                    failure_code=event.get("failure_code") if isinstance(event.get("failure_code"), str) else None,
                )
                response = {
                    "type": "command_preflight_result",
                    "command_id": str(command.id),
                    "status": command.status,
                    "preflight_status": command.preflight_status,
                    "preflight_origin": command.preflight_origin,
                    "preflight_reason": command.preflight_reason,
                }
                await websocket.send_json(response)
                await bridge_transport_registry.publish_event(command.id, response)
                continue

            if event_type in {"command_started", "command_progress", "command_evidence"}:
                typed_event = {
                    "type": event_type,
                    "command_id": str(command_id),
                    "detail": str(event.get("detail") or "")[:512],
                    "evidence": event.get("evidence") if isinstance(event.get("evidence"), dict) else {},
                }
                await bridge_transport_registry.publish_event(command_id, typed_event)
                continue

            if event_type == "command_cancel":
                command = await bridge.cancel_command(
                    device=device,
                    command_id=command_id,
                    reason=str(event.get("reason") or "The local browser operator was stopped by the user.")[:1000],
                )
                response = {"type": "command_cancelled", "command_id": str(command.id), "status": command.status}
                await websocket.send_json(response)
                await bridge_transport_registry.publish_event(command.id, response)
                continue

            if event_type in {"command_completed", "command_failed"}:
                failed = event_type == "command_failed"
                try:
                    command = await bridge.complete_command(
                        device=device,
                        command_id=command_id,
                        result=event.get("result") if isinstance(event.get("result"), dict) else {},
                        evidence=event.get("evidence") if isinstance(event.get("evidence"), dict) else {},
                        verification=event.get("verification") if isinstance(event.get("verification"), dict) else {},
                        lease_token=str(event.get("lease_token") or "")[:128] or None,
                        error=(str(event.get("error") or "")[:1000] or None) if failed else None,
                        cancelled=bool(event.get("cancelled")),
                        cancel_reason=str(event.get("cancel_reason") or "")[:1000] or None,
                    )
                except HTTPException as exc:
                    # Raising HTTPException on a WebSocket yields ASGI
                    # "websocket.http.response.start" and a reconnect storm.
                    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                    await websocket.send_json({
                        "type": "bridge_error",
                        "error_code": detail.get("code") or "complete_failed",
                        "message": detail.get("message") or str(exc.detail),
                        "command_id": str(command_id),
                        "status_code": exc.status_code,
                    })
                    continue
                response = {"type": event_type, "command_id": str(command.id), "status": command.status}
                await websocket.send_json(response)
                await bridge_transport_registry.publish_event(command.id, response)
                continue

            if event_type == "on_browser_event":
                event_payload = event.get("payload", {}) if isinstance(event.get("payload"), dict) else {}
                await bridge.forward_operation_event(device=device, event=event_payload)
                await websocket.send_json({"type": "browser_event_ack", "ok": True})
                continue

            await websocket.send_json({"type": "bridge_error", "error_code": "unsupported_event", "message": "Unsupported Onsite48 Bridge transport event."})
    except WebSocketDisconnect:
        return
    except asyncio.TimeoutError:
        await websocket.close(code=4401, reason="Authentication timed out.")
        return
    finally:
        if device is not None:
            await bridge_transport_registry.unregister(device.id, websocket)


@router.post("/extension/authorize")
async def authorize_extension(
    payload: AuthorizeRequest,
    request: Request,
    device: BrowserBridgeDevice = Depends(bridge_device),
    session: AsyncSession = Depends(get_db_session),
):
    """Authorize Onsite48 Bridge to operate on any tab in the current session."""
    device.session_state = "ACTIVE"
    device.authorized_origins = json.dumps(payload.origins)
    device.authorized_capabilities = json.dumps(payload.capabilities)
    device.session_authorized_at = datetime.utcnow()
    await session.commit()
    return {"authorized": True, "session_state": "ACTIVE"}


@router.post("/extension/context")
async def update_extension_context(
    payload: ActiveContextRequest,
    request: Request,
    device: BrowserBridgeDevice = Depends(bridge_device),
):
    context = await request.app.state.browser_bridge.update_active_context(
        device=device,
        url=payload.url,
        title=payload.title,
        tab_id=payload.tab_id,
        window_id=payload.window_id,
        selected_at=payload.selected_at,
        selection_state=payload.selection_state,
    )
    return {"active_context": context}


@router.get("/extension/commands")
async def poll_extension_commands(
    request: Request,
    wait_seconds: int = Query(default=0, ge=0, le=25),
    device: BrowserBridgeDevice = Depends(bridge_device),
):
    commands = await request.app.state.browser_bridge.wait_for_commands(device, wait_seconds=wait_seconds)
    return {
        "commands": [
            {
                "id": str(command.id),
                "command_type": command.command_type,
                "payload": command.payload,
                "target_tab": (command.payload or {}).get("target_tab") if isinstance((command.payload or {}).get("target_tab"), dict) else None,
                "requires_approval": command.requires_approval,
                "preflight_status": command.preflight_status,
                "preflight_origin": command.preflight_origin,
                "preflight_reason": command.preflight_reason,
                "created_at": command.created_at.isoformat(),
            }
            for command in commands
        ]
    }


@router.post("/extension/commands/{command_id}/preflight")
async def preflight_extension_command(
    command_id: uuid.UUID,
    payload: CommandPreflightRequest,
    request: Request,
    device: BrowserBridgeDevice = Depends(bridge_device),
):
    command = await request.app.state.browser_bridge.preflight_command(
        device=device,
        command_id=command_id,
        ready=payload.ready,
        origin=payload.origin,
        reason=payload.reason,
        failure_code=payload.failure_code,
    )
    return {
        "id": str(command.id),
        "status": command.status,
        "preflight_status": command.preflight_status,
        "preflight_origin": command.preflight_origin,
        "lease_token": command.lease_token,
        "lease_expires_at": command.lease_expires_at.isoformat() if command.lease_expires_at else None,
    }


@router.post("/extension/commands/{command_id}/cancel")
async def cancel_extension_command(
    command_id: uuid.UUID,
    payload: CommandCancelRequest,
    request: Request,
    device: BrowserBridgeDevice = Depends(bridge_device),
):
    command = await request.app.state.browser_bridge.cancel_command(
        device=device,
        command_id=command_id,
        reason=payload.reason,
    )
    return {"id": str(command.id), "status": command.status}


@router.post("/extension/commands/{command_id}/complete")
async def complete_extension_command(
    command_id: uuid.UUID,
    payload: CommandCompletionRequest,
    request: Request,
    device: BrowserBridgeDevice = Depends(bridge_device),
):
    command = await request.app.state.browser_bridge.complete_command(
        device=device,
        command_id=command_id,
        result=payload.result,
        evidence=payload.evidence,
        verification=payload.verification,
        lease_token=payload.lease_token,
        error=payload.error,
        cancelled=payload.cancelled,
        cancel_reason=payload.cancel_reason,
    )
    return {"id": str(command.id), "status": command.status}


@router.post("/task-context")
async def update_task_context(
    payload: TaskContextRequest,
    request: Request,
    device: BrowserBridgeDevice = Depends(bridge_device),
):
    await request.app.state.browser_bridge.update_task_context(
        device=device,
        context={
            "taskId": payload.device_id,
            "threadId": payload.thread_id,
            "taskGoal": payload.task_goal,
            "plannedSteps": payload.planned_steps,
            "completedSteps": payload.completed_steps,
            "currentStep": payload.current_step,
            "tabHistory": payload.tab_history,
            "activeTabId": payload.active_tab_id,
            "lockedTabId": payload.locked_tab_id,
            "sessionStartAt": payload.session_start_at,
            "status": payload.status,
        },
    )
    return {"ok": True}


@router.post("/operation-event")
async def record_operation_event(
    payload: OperationEventRequest,
    request: Request,
    device: BrowserBridgeDevice = Depends(bridge_device),
    session: AsyncSession = Depends(get_db_session),
):
    event = OperationEvent(
        device_id=device.id,
        organization_id=device.organization_id,
        user_id=device.user_id,
        thread_id=payload.thread_id,
        event_type=payload.event_type,
        event_data=json.dumps(payload.event_data),
    )
    session.add(event)
    await session.commit()
    await request.app.state.browser_bridge.forward_operation_event(device=device, event=payload.event_data)
    return {"ok": True}


@router.post("/session-state")
async def update_session_state(
    payload: SessionStateRequest,
    request: Request,
    device: BrowserBridgeDevice = Depends(bridge_device),
):
    await request.app.state.browser_bridge.update_session_from_extension(
        device=device,
        state={"sessionState": payload.session_state, "pauseReason": payload.pause_reason},
    )
    return {"session_state": payload.session_state, "pause_reason": payload.pause_reason}


@router.post("/session-state/poll")
async def poll_session_state(
    payload: PollSessionStateRequest,
    request: Request,
    device: BrowserBridgeDevice = Depends(bridge_device),
):
    return {"session_state": device.session_state, "pause_reason": device.pause_reason}


@router.post("/route-command")
async def route_command(
    payload: RouteCommandRequest,
    request: Request,
    device: BrowserBridgeDevice = Depends(bridge_device),
    session: AsyncSession = Depends(get_db_session),
):
    from urllib.parse import urlparse as _urlparse
    target_hostname = _urlparse(payload.target_url).hostname or ""
    all_devices = list((await session.scalars(select(BrowserBridgeDevice).where(
        BrowserBridgeDevice.organization_id == device.organization_id,
        BrowserBridgeDevice.user_id == device.user_id,
        BrowserBridgeDevice.status == "connected",
        BrowserBridgeDevice.revoked_at.is_(None),
    ).order_by(BrowserBridgeDevice.created_at.asc()))).all())
    target_device = None
    for d in all_devices:
        ctx = d.active_context or {}
        if ctx.get("hostname") == target_hostname:
            target_device = d
            break
    if target_device is None:
        from sqlalchemy import exists as _exists
        has_trust = await session.scalar(select(_exists(BrowserBridgeTrustRule)).where(
            BrowserBridgeTrustRule.device_id.in_([d.id for d in all_devices]),
            BrowserBridgeTrustRule.hostname == target_hostname,
            BrowserBridgeTrustRule.enabled.is_(True),
        ))
        if has_trust:
            target_device = all_devices[0] if all_devices else device
    if target_device is None:
        target_device = device
    return {"tab_id": None, "url": payload.target_url, "created": False, "routed_device_id": str(target_device.id)}


@router.post("/wait-for-challenge")
async def wait_for_challenge(
    payload: WaitForChallengeRequest,
    request: Request,
    device: BrowserBridgeDevice = Depends(bridge_device),
):
    from re import search as _re_search
    deadline = asyncio.get_running_loop().time() + min(payload.timeout_seconds, 120)
    while asyncio.get_running_loop().time() < deadline:
        ctx = device.active_context or {}
        url = ctx.get("url") or ""
        title = ctx.get("title") or ""
        if url:
            content = (title or "") + " " + url
            if _re_search(r"captcha|recaptcha|hcaptcha|cloudflare.*challenge", content, re.IGNORECASE):
                return {"detected": True, "challenge_type": "captcha", "severity": "block"}
            if _re_search(r"mfa|two.factor|two.step|verification code|otp", content, re.IGNORECASE):
                return {"detected": True, "challenge_type": "mfa", "severity": "block"}
            if _re_search(r"login|sign.in|password", content, re.IGNORECASE):
                return {"detected": True, "challenge_type": "login_required", "severity": "pause"}
        await asyncio.sleep(1)
    return {"detected": False}


@router.post("/browser-event")
async def on_browser_event(
    payload: OnBrowserEventRequest,
    request: Request,
    device: BrowserBridgeDevice = Depends(bridge_device),
):
    await request.app.state.browser_bridge.forward_operation_event(device=device, event=payload.payload)
    return {"ok": True}