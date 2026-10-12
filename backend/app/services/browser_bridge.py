"""Security-first coordination service for the user-owned Onsite48 Bridge extension.

The extension never sends browser cookies, saved passwords, or a broad account
token. Commands remain pending until the extension proves it has permission for
the exact browser context. Task approval, browser permission, device affinity,
and command execution are intentionally distinct states.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import BrowserBridgeCommand, BrowserBridgeDevice, BrowserBridgeTrustRule, BrowserSession, TaskThread, TaskContext, OperationEvent
from app.services.bridge_transport import bridge_transport_registry

PAIRING_TTL = timedelta(minutes=10)
COMMAND_TTL = timedelta(minutes=5)
COMMAND_CLAIM_LEASE = timedelta(minutes=3)
OPERATOR_COMMAND_LEASE = timedelta(minutes=3)
ACTIVE_CONTEXT_TTL = timedelta(minutes=2)
SAFE_TRUSTABLE_COMMANDS = {
    "read_active_tab", "inspect_active_tab", "scroll_active_tab", "observe_surface", "capture_active_tab", "list_tabs",
    # New task-owned workspace commands (read-only)
    "observe", "inspect", "locate", "screenshot", "read_page", "list_task_tabs",
}
# Commands that inject into, read from, or otherwise operate on a specific
# local page resolve the currently focused public tab at execution time.
# Explicit tab activation remains separately approved; it is never a hidden lock.
PAGE_BOUND_COMMANDS = {
    "read_active_tab", "inspect_active_tab", "scroll_active_tab", "observe_surface", "navigate_active_tab",
    "capture_active_tab", "snapshot_active_tab", "click_active_tab", "fill_active_tab", "download_active_tab", "upload_active_tab", "control_media",
    # New task-owned workspace commands (page-bound)
    "observe", "inspect", "locate", "screenshot", "read_page", "navigate", "click", "type", "scroll",
    "back", "forward", "reload", "double_click", "right_click", "hover", "move_mouse",
    "insert_text", "press_key", "key_down", "key_up", "hotkey", "scroll_to",
    "drag", "drag_and_drop", "select_option", "check", "uncheck", "focus",
    "wait", "wait_for_url", "wait_for_text", "wait_for_selector", "wait_for_navigation",
    "wait_for_network_idle", "wait_for_dom_stable", "wait_for_download", "wait_for_dialog",
    "control_media", "download", "upload",
}
SELECTION_FAILURE_CODES = {
    "selected_tab_required", "selected_tab_closed", "selected_tab_permission_required", "selected_tab_mismatch",
}
ALLOWED_CAPABILITIES = SAFE_TRUSTABLE_COMMANDS | {
    "activate_tab", "open_tab", "click_active_tab", "fill_active_tab", "download_active_tab", "upload_active_tab", "control_media", "open_task_tabs", "close_task_tabs",
    # New task-owned workspace commands (user gesture / interact)
    "create_task_workspace", "claim_task_workspace", "open_task_tab", "close_task_tab", "close_task_workspace",
    "navigate", "click", "double_click", "right_click", "type", "insert_text", "press_key", "hotkey",
    "drag", "drag_and_drop", "upload", "download", "takeover", "resume", "stop",
    "back", "forward", "reload", "hover", "move_mouse", "key_down", "key_up",
    "scroll_to", "select_option", "check", "uncheck", "focus",
    "wait", "wait_for_url", "wait_for_text", "wait_for_selector", "wait_for_navigation",
    "wait_for_network_idle", "wait_for_dom_stable", "wait_for_download", "wait_for_dialog",
}
ALLOWED_COMMANDS = ALLOWED_CAPABILITIES


class BridgePermissionRequired(RuntimeError):
    """The approved task is waiting for a user-granted browser-site permission."""

    def __init__(self, *, origin: str | None, command_id: uuid.UUID) -> None:
        self.origin = origin or "the selected website"
        self.command_id = command_id
        super().__init__(f"Onsite48 Bridge requires site permission for {self.origin}. Open the extension on the focused page, approve this exact site, then resume the paused task.")


class BridgeSelectionRequired(RuntimeError):
    """A page command has no safe, fresh, user-selected local browser tab."""

    def __init__(self, *, code: str, message: str, command_id: uuid.UUID | None = None) -> None:
        self.code = code if code in SELECTION_FAILURE_CODES else "selected_tab_required"
        self.command_id = command_id
        super().__init__(message)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalized_hostname(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("A complete public http or https URL is required.")
    return parsed.hostname.lower().rstrip(".")


def normalized_origin(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("A complete public http or https origin is required.")
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname.lower().rstrip('.')}{port}"


def extension_command_payload(command: BrowserBridgeCommand) -> dict[str, Any]:
    """Return the minimum command envelope shared by push and poll delivery."""
    payload = command.payload or {}
    return {
        "id": str(command.id),
        "task_id": str(command.task_id) if command.task_id else None,
        "command_type": command.command_type,
        "payload": payload,
        "target_tab": payload.get("target_tab") if isinstance(payload.get("target_tab"), dict) else None,
        "requires_approval": command.requires_approval,
        "preflight_status": command.preflight_status,
        "preflight_origin": command.preflight_origin,
        "preflight_reason": command.preflight_reason,
        "lease_token": command.lease_token,
        "lease_expires_at": command.lease_expires_at.isoformat() if command.lease_expires_at else None,
        "created_at": command.created_at.isoformat(),
        # Include session_id and sequence for command ordering
        "session_id": str(command.session_id) if command.session_id else None,
        "session_sequence": command.sequence,
        # Include verification rules if present
        "verification": command.payload.get("verification", []) if command.payload else [],
    }


class BrowserBridgeService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory
        # Ephemeral device-pair sessions (Connect button). Multi-worker deploys
        # should back this with Redis; single-process and dev work in-memory.
        # Keyed by poll_token; secondary index by user_code for Settings confirm.
        self._pending_device_pairs: dict[str, dict[str, Any]] = {}
        self._pending_by_user_code: dict[str, str] = {}

    async def bind_browser_session(self, *, session_id: uuid.UUID, organization_id: uuid.UUID, user_id: uuid.UUID, device: BrowserBridgeDevice, task_id: uuid.UUID | None, capabilities: list[str] | None = None) -> None:
        """Attach a user-created BrowserSession to the device selected by bridge affinity.

        The browser device is already authenticated by the bridge token and
        selected using the task/device affinity rules. This closes the gap
        between the chat-created session and the first extension command while
        rejecting cross-device rebinding.
        """
        async with self.session_factory() as session:
            item = await session.scalar(select(BrowserSession).where(
                BrowserSession.id == session_id,
                BrowserSession.organization_id == organization_id,
                BrowserSession.user_id == user_id,
            ).with_for_update())
            if item is None:
                raise RuntimeError("The requested browser session no longer exists.")
            if item.device_id is not None and item.device_id != device.id:
                raise RuntimeError("The browser session is already bound to a different paired browser.")
            item.device_id = device.id
            if item.task_id is None and task_id is not None:
                item.task_id = task_id
            if not item.granted_capabilities and capabilities:
                item.granted_capabilities = sorted(set(str(value) for value in capabilities if str(value).strip()))[:20]
            if item.status == "authorizing":
                item.status = "authorized"
            item.lease_expires_at = utcnow() + OPERATOR_COMMAND_LEASE
            await session.commit()

    async def create_pairing(self, *, organization_id: uuid.UUID, user_id: uuid.UUID, display_name: str) -> tuple[BrowserBridgeDevice, str]:
        pairing_code = secrets.token_urlsafe(24)
        device = BrowserBridgeDevice(
            organization_id=organization_id,
            user_id=user_id,
            device_id=secrets.token_urlsafe(18),
            display_name=display_name.strip()[:160] or "My browser",
            status="pending",
            pairing_secret_hash=digest(pairing_code),
            granted_capabilities=[],
        )
        async with self.session_factory() as session:
            session.add(device)
            await session.commit()
            await session.refresh(device)
        return device, pairing_code

    def _purge_expired_pending_pairs(self) -> None:
        now = utcnow()
        expired_tokens = [
            token for token, row in self._pending_device_pairs.items()
            if row.get("expires_at") and row["expires_at"] < now
        ]
        for token in expired_tokens:
            row = self._pending_device_pairs.pop(token, None) or {}
            code = row.get("user_code")
            if code and self._pending_by_user_code.get(code) == token:
                self._pending_by_user_code.pop(code, None)

    def start_device_pair(self, *, display_name: str) -> dict[str, Any]:
        """Extension Connect: create an ephemeral pending pair (not yet bound to a user)."""
        self._purge_expired_pending_pairs()
        device_id = secrets.token_urlsafe(18)
        poll_token = secrets.token_urlsafe(24)
        # Short human code for Settings → My Browser confirm (e.g. A1B2C3)
        user_code = secrets.token_hex(3).upper()
        # Avoid rare collisions on the short code
        while user_code in self._pending_by_user_code:
            user_code = secrets.token_hex(3).upper()
        expires_at = utcnow() + PAIRING_TTL
        name = (display_name or "Chrome · Onsite48 Browser").strip()[:160] or "Chrome · Onsite48 Browser"
        self._pending_device_pairs[poll_token] = {
            "device_id": device_id,
            "poll_token": poll_token,
            "user_code": user_code,
            "display_name": name,
            "expires_at": expires_at,
            "confirmed": False,
            "organization_id": None,
            "user_id": None,
        }
        self._pending_by_user_code[user_code] = poll_token
        return {
            "device_id": device_id,
            "user_code": user_code,
            "poll_token": poll_token,
            "verification_uri": "/settings/connectors/my-browser",
            "expires_in_seconds": int(PAIRING_TTL.total_seconds()),
            "device": {
                "device_id": device_id,
                "status": "pending",
                "display_name": name,
            },
        }

    async def confirm_device_pair(
        self,
        *,
        user_code: str,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Product Settings: bind a pending Connect attempt to the signed-in user.

        Creates the durable pending BrowserBridgeDevice row so the extension can
        finish via complete_device_pair with the poll_token as pairing secret.
        """
        self._purge_expired_pending_pairs()
        code = (user_code or "").strip().upper()
        poll_token = self._pending_by_user_code.get(code)
        if not poll_token:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No pending browser pair matches that code. Click Connect in the extension again.",
            )
        row = self._pending_device_pairs.get(poll_token)
        if not row or row["expires_at"] < utcnow():
            self._pending_by_user_code.pop(code, None)
            self._pending_device_pairs.pop(poll_token, None)
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="That pairing code expired. Click Connect in the extension again.",
            )

        device_id = row["device_id"]
        async with self.session_factory() as session:
            existing = await session.scalar(
                select(BrowserBridgeDevice).where(BrowserBridgeDevice.device_id == device_id)
            )
            if existing is not None and existing.status == "connected" and existing.revoked_at is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This browser is already connected.",
                )
            if existing is None:
                device = BrowserBridgeDevice(
                    organization_id=organization_id,
                    user_id=user_id,
                    device_id=device_id,
                    display_name=row["display_name"],
                    status="pending",
                    # Extension proves possession of poll_token on complete.
                    pairing_secret_hash=digest(poll_token),
                    granted_capabilities=[],
                    active_context={"user_code": code, "device_pair": "awaiting_extension"},
                )
                session.add(device)
            else:
                device = existing
                device.organization_id = organization_id
                device.user_id = user_id
                device.display_name = row["display_name"]
                device.status = "pending"
                device.pairing_secret_hash = digest(poll_token)
                device.revoked_at = None
                device.active_context = {**(device.active_context or {}), "user_code": code, "device_pair": "awaiting_extension"}
            await session.commit()
            await session.refresh(device)

        row["confirmed"] = True
        row["organization_id"] = str(organization_id)
        row["user_id"] = str(user_id)
        return {
            "device": {
                "id": str(device.id),
                "device_id": device.device_id,
                "display_name": device.display_name,
                "status": device.status,
            },
            "user_code": code,
            "message": "Pairing confirmed. The extension will finish connecting automatically.",
        }

    async def complete_device_pair(
        self,
        *,
        device_id: str,
        poll_token: str,
        extension_name: str = "Onsite48 Browser",
        requested_capabilities: list[str] | None = None,
    ) -> tuple[BrowserBridgeDevice, str] | dict[str, Any]:
        """Extension finishes Connect after the user confirmed in Onsite48.

        Unauthenticated: possession of poll_token is the proof. Returns either
        (device, bridge_token) or a wait payload when confirmation is still pending.
        """
        self._purge_expired_pending_pairs()
        row = self._pending_device_pairs.get(poll_token)
        if row is None or row.get("device_id") != device_id:
            # May already be confirmed+completed (token cleared); fall through to DB.
            pass
        elif row["expires_at"] < utcnow():
            self._pending_device_pairs.pop(poll_token, None)
            code = row.get("user_code")
            if code:
                self._pending_by_user_code.pop(code, None)
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Pairing expired. Click Connect in the extension again.",
            )
        elif not row.get("confirmed"):
            return {
                "status": "awaiting_confirmation",
                "user_code": row.get("user_code"),
                "message": (
                    f"Confirm code {row.get('user_code')} in Onsite48 "
                    "(Settings → Connectors → My Browser), then this will finish."
                ),
            }

        caps = requested_capabilities or [
            "observe", "navigate", "click", "type", "scroll",
            "screenshot", "download", "upload", "takeover", "workspace",
        ]
        try:
            device, token = await self.pair_extension(
                device_id=device_id,
                pairing_code=poll_token,
                extension_name=extension_name,
                requested_capabilities=caps,
            )
        except HTTPException as exc:
            if exc.status_code == 404 and row and not row.get("confirmed"):
                return {
                    "status": "awaiting_confirmation",
                    "user_code": row.get("user_code"),
                    "message": (
                        f"Confirm code {row.get('user_code')} in Onsite48 "
                        "(Settings → Connectors → My Browser), then this will finish."
                    ),
                }
            raise

        # Clear pending once connected
        if poll_token in self._pending_device_pairs:
            code = self._pending_device_pairs[poll_token].get("user_code")
            self._pending_device_pairs.pop(poll_token, None)
            if code:
                self._pending_by_user_code.pop(code, None)
        return device, token

    async def pair_extension(self, *, device_id: str, pairing_code: str, extension_name: str, requested_capabilities: list[str]) -> tuple[BrowserBridgeDevice, str]:
        now = utcnow()
        async with self.session_factory() as session:
            device = await session.scalar(select(BrowserBridgeDevice).where(BrowserBridgeDevice.device_id == device_id))
            if device is None or device.status != "pending" or not device.pairing_secret_hash:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The pairing request is unavailable or has already been used.")
            if device.created_at + PAIRING_TTL < now:
                device.status = "expired"
                device.pairing_secret_hash = None
                await session.commit()
                raise HTTPException(status_code=status.HTTP_410_GONE, detail="The pairing code expired. Create a new pairing request in Onsite48.")
            if not secrets.compare_digest(device.pairing_secret_hash, digest(pairing_code)):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="The pairing code is invalid.")
            capabilities = sorted(set(requested_capabilities) & ALLOWED_CAPABILITIES)
            if not capabilities:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The extension did not request a supported Onsite48 Bridge capability.")
            has_connected_device = await session.scalar(select(BrowserBridgeDevice.id).where(
                BrowserBridgeDevice.organization_id == device.organization_id,
                BrowserBridgeDevice.user_id == device.user_id,
                BrowserBridgeDevice.status == "connected",
                BrowserBridgeDevice.revoked_at.is_(None),
            ).limit(1))
            raw_token = secrets.token_urlsafe(48)
            device.display_name = extension_name.strip()[:160] or device.display_name
            device.status = "connected"
            device.pairing_secret_hash = None
            device.access_token_hash = digest(raw_token)
            device.granted_capabilities = capabilities
            # The first paired browser becomes the explicit default. Pairing an
            # additional browser never silently changes where tasks are routed.
            device.is_preferred = has_connected_device is None
            device.paired_at = now
            device.last_seen_at = now
            await session.commit()
            await session.refresh(device)
            return device, raw_token

    async def authenticate_extension(self, token: str) -> BrowserBridgeDevice:
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="A Onsite48 Bridge token is required.")
        async with self.session_factory() as session:
            device = await session.scalar(select(BrowserBridgeDevice).where(
                BrowserBridgeDevice.access_token_hash == digest(token),
                BrowserBridgeDevice.status == "connected",
                BrowserBridgeDevice.revoked_at.is_(None),
            ))
            if device is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="The Onsite48 Bridge device is disconnected or revoked.")
            device.last_seen_at = utcnow()
            await session.commit()
            return device

    async def update_active_context(
        self,
        *,
        device: BrowserBridgeDevice,
        url: str,
        title: str = "",
        tab_id: int | None = None,
        window_id: int | None = None,
        selected_at: str | None = None,
        selection_state: str | None = None,
    ) -> dict[str, Any]:
        """Persist only the currently user-selected page identity and bounded metadata."""
        state = str(selection_state or "selected").strip().lower()
        now = utcnow()
        if state != "selected":
            context = {
                "selection_state": state or "unavailable",
                "observed_at": now.isoformat(),
                "selected_at": str(selected_at or "")[:64] or None,
            }
        else:
            hostname = normalized_hostname(url)
            if not isinstance(tab_id, int) or tab_id < 0:
                raise ValueError("A selected browser tab identifier is required.")
            if not isinstance(window_id, int) or window_id < 0:
                raise ValueError("A selected browser window identifier is required.")
            context = {
                "selection_state": "selected",
                "tab_id": tab_id,
                "window_id": window_id,
                "hostname": hostname,
                "title": title.strip()[:240],
                "selected_at": str(selected_at or now.isoformat())[:64],
                "observed_at": now.isoformat(),
            }
        async with self.session_factory() as session:
            current = await session.get(BrowserBridgeDevice, device.id)
            if current is None or current.status != "connected":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The paired browser is no longer connected.")
            current.active_context = context
            current.last_seen_at = now
            if state == "selected":
                history = json.loads(current.active_context_history or "[]")
                history.append({
                    "tab_id": context.get("tab_id"),
                    "url": context.get("url"),
                    "title": context.get("title"),
                    "hostname": context.get("hostname"),
                    "window_id": context.get("window_id"),
                    "observed_at": context.get("observed_at"),
                })
                current.active_context_history = json.dumps(history[-10:])
            await session.commit()
        return context

    @staticmethod
    def _recent_context(device: BrowserBridgeDevice) -> dict[str, Any] | None:
        context = device.active_context or {}
        if context.get("selection_state") != "selected":
            return None
        hostname = str(context.get("hostname") or "").lower().strip()
        observed_at = context.get("observed_at")
        tab_id = context.get("tab_id")
        window_id = context.get("window_id")
        if not hostname or not observed_at or not isinstance(tab_id, int) or tab_id < 0 or not isinstance(window_id, int) or window_id < 0:
            return None
        try:
            observed = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
        except ValueError:
            return None
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        if utcnow() - observed > ACTIVE_CONTEXT_TTL:
            return None
        return {
            "hostname": hostname,
            "title": str(context.get("title") or ""),
            "tab_id": tab_id,
            "window_id": window_id,
            "selected_at": str(context.get("selected_at") or ""),
            "observed_at": observed.isoformat(),
        }

    @staticmethod
    def _selection_error(device: BrowserBridgeDevice) -> BridgeSelectionRequired:
        context = device.active_context or {}
        state = str(context.get("selection_state") or "selected_tab_required")
        if state == "closed":
            return BridgeSelectionRequired(
                code="selected_tab_closed",
                message="The focused browser page was closed. Focus the intended page, approve its site in Onsite48 Bridge, then resume the task.",
            )
        if state == "permission_required":
            return BridgeSelectionRequired(
                code="selected_tab_permission_required",
                message="The focused browser page still needs site permission. Approve its exact site in Onsite48 Bridge, then resume the task.",
            )
        return BridgeSelectionRequired(
            code="selected_tab_required",
            message="Focus the intended browser page and resume the task. Onsite48 Bridge follows the focused public tab; it does not require a locked tab.",
        )

    async def update_task_context(self, *, device: BrowserBridgeDevice, context: dict[str, Any]) -> dict[str, Any]:
        async with self.session_factory() as session:
            current = await session.get(BrowserBridgeDevice, device.id)
            if current is None or current.status != "connected":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The paired browser is no longer connected.")
            task_id = context.get("taskId")
            task = None
            if task_id:
                task = await session.scalar(select(TaskContext).where(TaskContext.device_id == device.id, TaskContext.task_id == task_id))
            if task:
                for field in ("task_goal", "planned_steps", "completed_steps", "current_step", "tab_history", "active_tab_id", "locked_tab_id", "status"):
                    if field in context:
                        setattr(task, field, context[field])
                task.updated_at = utcnow()
            else:
                task = TaskContext(
                    device_id=device.id, organization_id=device.organization_id, user_id=device.user_id,
                    thread_id=context.get("threadId"), task_goal=context.get("taskGoal"),
                    planned_steps=context.get("plannedSteps", "[]"), completed_steps=context.get("completedSteps", "[]"),
                    current_step=context.get("currentStep"), tab_history=context.get("tabHistory", "[]"),
                    active_tab_id=context.get("activeTabId"), locked_tab_id=context.get("lockedTabId"),
                    session_start_at=context.get("sessionStartAt"), status=context.get("status", "running"),
                )
                session.add(task)
            await session.commit()
            return {"ok": True}

    async def forward_operation_event(self, *, device: BrowserBridgeDevice, event: dict[str, Any]) -> None:
        await bridge_transport_registry.send_browser_event(device.id, event)

    async def update_session_from_extension(self, *, device: BrowserBridgeDevice, state: dict[str, Any]) -> None:
        async with self.session_factory() as session:
            current = await session.get(BrowserBridgeDevice, device.id)
            if current is None:
                return
            session_state = state.get("sessionState")
            if session_state == "PAUSED_CHALLENGE":
                current.session_state = "PAUSED_CHALLENGE"
                current.pause_reason = "security_challenge"
            elif session_state == "ACTIVE" and current.session_state == "PAUSED_CHALLENGE":
                current.session_state = "ACTIVE"
                current.pause_reason = None
            await session.commit()

    @staticmethod
    def _unavailable_result_error(result: dict[str, Any]) -> str | None:
        if not isinstance(result, dict):
            return "Onsite48 Bridge returned an invalid local-browser result."
        if result.get("available") is False:
            return str(result.get("reason") or result.get("error") or "The selected browser tab was unavailable.")[:1000]
        code = str(result.get("failure_code") or "")
        if code in SELECTION_FAILURE_CODES:
            return str(result.get("reason") or "The selected browser tab is no longer available. Select/Sync the intended tab, then resume the task.")[:1000]
        return None

    async def _task_id_from_external_thread(
        self, session: AsyncSession, *, organization_id: uuid.UUID, user_id: uuid.UUID, task_thread_id: str | None
    ) -> uuid.UUID | None:
        if not task_thread_id:
            return None
        task = await session.scalar(select(TaskThread).where(
            TaskThread.external_thread_id == task_thread_id,
            TaskThread.organization_id == organization_id,
            TaskThread.user_id == user_id,
        ))
        if task is None:
            raise RuntimeError("The originating Synth task could not be resolved for this local-browser action.")
        return task.id

    async def _select_bound_device(
        self, session: AsyncSession, *, organization_id: uuid.UUID, user_id: uuid.UUID, task_id: uuid.UUID | None
    ) -> BrowserBridgeDevice:
        if task_id is not None:
            bound_device_id = await session.scalar(select(BrowserBridgeCommand.device_id).where(
                BrowserBridgeCommand.task_id == task_id,
                BrowserBridgeCommand.organization_id == organization_id,
                BrowserBridgeCommand.user_id == user_id,
            ).order_by(BrowserBridgeCommand.created_at.asc()).limit(1))
            if bound_device_id is not None:
                device = await session.get(BrowserBridgeDevice, bound_device_id)
                if device is None or device.status != "connected" or device.revoked_at is not None:
                    raise RuntimeError("The browser bound to this task is unavailable. Reconnect it or start a new task with an available paired browser.")
                return device

        devices = list((await session.scalars(select(BrowserBridgeDevice).where(
            BrowserBridgeDevice.organization_id == organization_id,
            BrowserBridgeDevice.user_id == user_id,
            BrowserBridgeDevice.status == "connected",
            BrowserBridgeDevice.revoked_at.is_(None),
        ).order_by(BrowserBridgeDevice.created_at.asc()))).all())
        if not devices:
            raise RuntimeError("No paired browser is connected. Ask the user to pair Onsite48 Bridge and keep the extension online.")
        preferred = [device for device in devices if device.is_preferred]
        if len(preferred) == 1:
            return preferred[0]
        if len(devices) == 1:
            return devices[0]
        raise RuntimeError("More than one paired browser is connected. Select one preferred browser in Onsite48 Bridge settings before running a local-browser task.")

    async def trusted_capabilities(self, *, organization_id: uuid.UUID, user_id: uuid.UUID, device_id: uuid.UUID) -> tuple[str | None, set[str]]:
        async with self.session_factory() as session:
            device = await session.scalar(select(BrowserBridgeDevice).where(
                BrowserBridgeDevice.id == device_id,
                BrowserBridgeDevice.organization_id == organization_id,
                BrowserBridgeDevice.user_id == user_id,
                BrowserBridgeDevice.status == "connected",
                BrowserBridgeDevice.revoked_at.is_(None),
            ))
            if device is None:
                return None, set()
            context = self._recent_context(device)
            if context is None:
                return None, set()
            rule = await session.scalar(select(BrowserBridgeTrustRule).where(
                BrowserBridgeTrustRule.device_id == device.id,
                BrowserBridgeTrustRule.hostname == context["hostname"],
                BrowserBridgeTrustRule.enabled.is_(True),
            ))
            return context["hostname"], set(rule.allowed_capabilities or []) if rule else set()

    async def may_run_without_prompt(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID, command_type: str, payload: dict[str, Any], task_id: uuid.UUID | None = None
    ) -> tuple[bool, str | None]:
        if command_type not in SAFE_TRUSTABLE_COMMANDS:
            return False, None
        async with self.session_factory() as session:
            try:
                device = await self._select_bound_device(session, organization_id=organization_id, user_id=user_id, task_id=task_id)
            except RuntimeError:
                return False, None
            context = self._recent_context(device)
            if context is None:
                return False, None
            hostname = context["hostname"]
            if command_type == "navigate_active_tab":
                try:
                    if normalized_hostname(str(payload.get("url") or "")) != hostname:
                        return False, None
                except ValueError:
                    return False, None
            rule = await session.scalar(select(BrowserBridgeTrustRule).where(
                BrowserBridgeTrustRule.device_id == device.id,
                BrowserBridgeTrustRule.hostname == hostname,
                BrowserBridgeTrustRule.enabled.is_(True),
            ))
            return (bool(rule and command_type in set(rule.allowed_capabilities or [])), hostname if rule else None)

    async def list_trust_rules(self, *, organization_id: uuid.UUID, user_id: uuid.UUID) -> list[BrowserBridgeTrustRule]:
        async with self.session_factory() as session:
            return list((await session.scalars(select(BrowserBridgeTrustRule).where(
                BrowserBridgeTrustRule.organization_id == organization_id,
                BrowserBridgeTrustRule.user_id == user_id,
            ).order_by(BrowserBridgeTrustRule.hostname.asc()))).all())

    async def upsert_trust_rule(self, *, organization_id: uuid.UUID, user_id: uuid.UUID, device_id: uuid.UUID, hostname: str, allowed_capabilities: list[str], enabled: bool = True) -> BrowserBridgeTrustRule:
        hostname = normalized_hostname(f"https://{hostname}" if "://" not in hostname else hostname)
        capabilities = sorted(set(allowed_capabilities) & SAFE_TRUSTABLE_COMMANDS)
        if not capabilities:
            raise ValueError("Choose at least one read, inspect, scroll, navigation, or tab capability for this trusted site.")
        async with self.session_factory() as session:
            device = await session.scalar(select(BrowserBridgeDevice).where(
                BrowserBridgeDevice.id == device_id,
                BrowserBridgeDevice.organization_id == organization_id,
                BrowserBridgeDevice.user_id == user_id,
                BrowserBridgeDevice.status == "connected",
                BrowserBridgeDevice.revoked_at.is_(None),
            ))
            if device is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paired browser not found.")
            rule = await session.scalar(select(BrowserBridgeTrustRule).where(
                BrowserBridgeTrustRule.device_id == device.id,
                BrowserBridgeTrustRule.hostname == hostname,
            ))
            if rule is None:
                rule = BrowserBridgeTrustRule(device_id=device.id, organization_id=organization_id, user_id=user_id, hostname=hostname)
                session.add(rule)
            rule.allowed_capabilities = capabilities
            rule.enabled = enabled
            await session.commit()
            await session.refresh(rule)
            return rule

    async def delete_trust_rule(self, *, organization_id: uuid.UUID, user_id: uuid.UUID, rule_id: uuid.UUID) -> None:
        async with self.session_factory() as session:
            rule = await session.scalar(select(BrowserBridgeTrustRule).where(
                BrowserBridgeTrustRule.id == rule_id,
                BrowserBridgeTrustRule.organization_id == organization_id,
                BrowserBridgeTrustRule.user_id == user_id,
            ))
            if rule is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trusted-site rule not found.")
            await session.delete(rule)
            await session.commit()

    async def set_preferred_device(self, *, organization_id: uuid.UUID, user_id: uuid.UUID, device_id: uuid.UUID) -> BrowserBridgeDevice:
        async with self.session_factory() as session:
            async with session.begin():
                devices = list((await session.scalars(select(BrowserBridgeDevice).where(
                    BrowserBridgeDevice.organization_id == organization_id,
                    BrowserBridgeDevice.user_id == user_id,
                    BrowserBridgeDevice.status == "connected",
                    BrowserBridgeDevice.revoked_at.is_(None),
                ).with_for_update())).all())
                selected = next((device for device in devices if device.id == device_id), None)
                if selected is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connected paired browser not found.")
                for device in devices:
                    device.is_preferred = device.id == selected.id
            await session.refresh(selected)
            return selected

    async def _recover_stale_claims(self, session: AsyncSession, *, device_id: uuid.UUID, now: datetime) -> None:
        # Check if device has recent heartbeat before recovering stale claims
        device = await session.get(BrowserBridgeDevice, device_id)
        if device and device.last_seen_at:
            # If device was seen recently (within 2 minutes), don't recover claims
            # The extension may still be processing the command
            if (now - device.last_seen_at).total_seconds() < 120:
                return
        
        stale = list((await session.scalars(select(BrowserBridgeCommand).where(
            BrowserBridgeCommand.device_id == device_id,
            BrowserBridgeCommand.status == "claimed",
            BrowserBridgeCommand.claim_lease_expires_at.is_not(None),
            BrowserBridgeCommand.claim_lease_expires_at <= now,
        ).with_for_update(skip_locked=True))).all())
        for command in stale:
            command.status = "pending"
            command.claimed_at = None
            command.claim_lease_expires_at = None
            command.lease_token = None
            command.lease_expires_at = None
            command.preflight_status = "reclaimable"
            command.preflight_reason = "The local browser stopped responding after claiming this command. It is available to retry."
            command.preflight_at = now
        if stale:
            logger = __import__("structlog").get_logger("onsite48.browser_bridge")
            logger.warning("bridge.stale_claims_recovered", device_id=str(device_id), count=len(stale))

    async def list_commands_for_extension(self, device: BrowserBridgeDevice) -> list[BrowserBridgeCommand]:
        """Return pending commands without consuming them; preflight claims later."""
        now = utcnow()
        async with self.session_factory() as session:
            async with session.begin():
                await self._recover_stale_claims(session, device_id=device.id, now=now)
                stale = list((await session.scalars(select(BrowserBridgeCommand).where(
                    BrowserBridgeCommand.device_id == device.id,
                    BrowserBridgeCommand.status == "pending",
                    BrowserBridgeCommand.created_at < now - COMMAND_TTL,
                ).with_for_update(skip_locked=True))).all())
                for command in stale:
                    command.status = "expired"
                    command.error = "The paired browser did not respond before the command expired."
                    command.claim_lease_expires_at = None
                commands = list((await session.scalars(select(BrowserBridgeCommand).where(
                    BrowserBridgeCommand.device_id == device.id,
                    BrowserBridgeCommand.status == "pending",
                ).order_by(BrowserBridgeCommand.created_at.asc()).limit(10))).all())
                return commands

    async def wait_for_commands(self, device: BrowserBridgeDevice, *, wait_seconds: int = 0) -> list[BrowserBridgeCommand]:
        deadline = asyncio.get_running_loop().time() + max(0, min(wait_seconds, 25))
        while True:
            commands = await self.list_commands_for_extension(device)
            if commands or asyncio.get_running_loop().time() >= deadline:
                return commands
            await asyncio.sleep(0.5)

    async def preflight_command(
        self,
        *,
        device: BrowserBridgeDevice,
        command_id: uuid.UUID,
        ready: bool,
        origin: str | None,
        reason: str | None,
        failure_code: str | None = None,
    ) -> BrowserBridgeCommand:
        """Persist capability state, then atomically claim only a ready command."""
        now = utcnow()
        normalized = normalized_origin(origin) if origin else None
        async with self.session_factory() as session:
            async with session.begin():
                command = await session.scalar(select(BrowserBridgeCommand).where(
                    BrowserBridgeCommand.id == command_id,
                    BrowserBridgeCommand.device_id == device.id,
                ).with_for_update())
                if command is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bridge command not found for this paired browser.")
                if command.status in {"completed", "failed", "cancelled", "expired"}:
                    return command
                if command.status == "claimed":
                    return command
                if not ready:
                    code = str(failure_code or "")
                    command.preflight_status = code if code in SELECTION_FAILURE_CODES else "site_permission_required"
                    command.preflight_origin = normalized
                    command.preflight_reason = (reason or "This approved action requires access to the selected website.")[:512]
                    command.preflight_at = now
                    return command
                command.preflight_status = "ready"
                command.preflight_origin = normalized
                command.preflight_reason = None
                command.preflight_at = now
                command.status = "claimed"
                command.claimed_at = now
                command.claim_lease_expires_at = now + COMMAND_CLAIM_LEASE
                command.lease_token = secrets.token_urlsafe(24)
                command.lease_expires_at = now + OPERATOR_COMMAND_LEASE
            await session.refresh(command)
            return command

    async def complete_command(
        self,
        *,
        device: BrowserBridgeDevice,
        command_id: uuid.UUID,
        result: dict[str, Any],
        evidence: dict[str, Any] | None = None,
        verification: dict[str, Any] | None = None,
        lease_token: str | None = None,
        error: str | None = None,
        cancelled: bool = False,
        cancel_reason: str | None = None,
    ) -> BrowserBridgeCommand:
        async with self.session_factory() as session:
            command = await session.scalar(select(BrowserBridgeCommand).where(
                BrowserBridgeCommand.id == command_id,
                BrowserBridgeCommand.device_id == device.id,
            ).with_for_update())
            if command is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bridge command not found for this paired browser.")
            if command.status in {"completed", "failed", "cancelled", "expired"}:
                return command
            if command.status != "claimed":
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "stale_lease", "message": "This local-browser command was stopped, taken over, or is no longer active."})
            now = utcnow()
            # Always require valid lease_token for claimed commands
            if not lease_token:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "stale_lease", "message": "Lease token is required to complete a claimed command."})
            if (
                not command.lease_token
                or not secrets.compare_digest(command.lease_token, lease_token)
                or (command.lease_expires_at is not None and command.lease_expires_at <= now)
            ):
                await session.refresh(command)
                if command.status in {"completed", "failed", "cancelled", "expired"}:
                    return command
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "stale_lease", "message": "The local-browser command lease is no longer valid. Refresh the page state and request a new approved action."})
            if cancelled:
                command.status = "cancelled"
                command.cancelled_at = utcnow()
                command.cancel_reason = (cancel_reason or error or "The local browser operator was stopped by the user.")[:1000]
                command.error = command.cancel_reason
            else:
                unavailable_error = self._unavailable_result_error(result)
                terminal_error = error or unavailable_error
                command.status = "failed" if terminal_error else "completed"
                command.error = (terminal_error or "")[:1000] or None
            command.result = result if isinstance(result, dict) else {"value": result}
            command.evidence = evidence if isinstance(evidence, dict) else {}
            command.verification = verification if isinstance(verification, dict) else {}
            
            # Keep a compact, resumable operator checkpoint on the task. Never
            # persist cookies, credentials, screenshots, or unbounded page text.
            if command.task_id and command.command_type in {"observe_surface", "read_active_tab", "inspect_active_tab", "scroll_active_tab"}:
                task = await session.get(TaskThread, command.task_id, with_for_update=True)
                if task is not None:
                    metadata = dict(task.execution_metadata or {})
                    observation = result.get("result") if isinstance(result, dict) and isinstance(result.get("result"), dict) else result
                    if isinstance(observation, dict):
                        page = observation.get("page") if isinstance(observation.get("page"), dict) else observation
                        metadata["browser_operator"] = {
                            "phase": "observed",
                            "command_type": command.command_type,
                            "surface": observation.get("surface"),
                            "entry_count": min(int(observation.get("entry_count") or 0), 50),
                            "page_digest": str(page.get("page_digest") or "")[:128],
                            "url": str(page.get("url") or observation.get("url") or "")[:2048],
                            "title": str(page.get("title") or observation.get("title") or "")[:240],
                            "scroll_y": page.get("scroll_y") if isinstance(page.get("scroll_y"), (int, float)) else None,
                            "scroll_height": page.get("scroll_height") if isinstance(page.get("scroll_height"), (int, float)) else None,
                            "updated_at": utcnow().isoformat(),
                            "verification_status": verification.get("status") if verification else None,
                            "evidence_captured": bool(evidence),
                        }
                        task.execution_metadata = metadata
            command.completed_at = utcnow()
            command.claim_lease_expires_at = None
            command.lease_token = None
            command.lease_expires_at = None
            await session.commit()
            await session.refresh(command)
            return command

    async def cancel_command(self, *, device: BrowserBridgeDevice, command_id: uuid.UUID, reason: str) -> BrowserBridgeCommand:
        """Invalidate one active operator lease for Stop or Take over."""
        now = utcnow()
        async with self.session_factory() as session:
            command = await session.scalar(select(BrowserBridgeCommand).where(
                BrowserBridgeCommand.id == command_id,
                BrowserBridgeCommand.device_id == device.id,
            ).with_for_update())
            if command is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bridge command not found for this paired browser.")
            if command.status in {"completed", "failed", "cancelled", "expired"}:
                return command
            command.status = "cancelled"
            command.cancelled_at = now
            command.cancel_reason = reason[:1000]
            command.error = command.cancel_reason
            command.claim_lease_expires_at = None
            command.lease_token = None
            command.lease_expires_at = None
            await session.commit()
            await session.refresh(command)
            return command

    async def cancel_commands_for_task(self, *, task_id: uuid.UUID, reason: str) -> int:
        """Make task cancellation authoritative for all non-terminal owned commands."""
        now = utcnow()
        async with self.session_factory() as session:
            commands = list((await session.scalars(select(BrowserBridgeCommand).where(
                BrowserBridgeCommand.task_id == task_id,
                BrowserBridgeCommand.status.in_(("pending", "claimed")),
            ).with_for_update())).all())
            for command in commands:
                command.status = "cancelled"
                command.cancelled_at = now
                command.cancel_reason = reason[:1000]
                command.error = command.cancel_reason
                command.claim_lease_expires_at = None
                command.lease_token = None
                command.lease_expires_at = None
            if commands:
                await session.commit()
            return len(commands)

    # ─── VERIFICATION METHODS ──────────────────────────────────────

    async def verify_bridge_action(
        self,
        *,
        device: BrowserBridgeDevice,
        command_id: uuid.UUID,
        before_evidence: Dict[str, Any],
        after_evidence: Dict[str, Any],
        verification_rules: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Verify that a bridge action achieved its intended outcome."""
        if not verification_rules:
            return {
                "status": "inconclusive",
                "passed": 0,
                "failed": 0,
                "inconclusive": 1,
                "checks": [{"name": "no_rules", "passed": None, "detail": "No verification rules provided"}],
                "observed_at": utcnow().isoformat(),
            }
        
        results = []
        passed = 0
        failed = 0
        inconclusive = 0
        
        for rule in verification_rules:
            rule_type = rule.get("type")
            expected = rule.get("expected", {})
            required = rule.get("required", True)
            description = rule.get("description", "")
            
            try:
                if rule_type == "url_matches":
                    after_url = after_evidence.get("url", "")
                    expected_url = expected.get("url")
                    check_passed = expected_url and expected_url in after_url
                    detail = "URL matches expected" if check_passed else f"URL '{after_url}' does not match expected"
                    results.append({"name": "url_matches", "passed": check_passed, "detail": detail})
                    if check_passed: passed += 1
                    else: failed += 1 if required else 0
                    
                elif rule_type == "title_matches":
                    after_title = after_evidence.get("title", "")
                    expected_title = expected.get("title")
                    check_passed = expected_title and expected_title in after_title
                    detail = "Title matches expected" if check_passed else f"Title '{after_title}' does not match expected"
                    results.append({"name": "title_matches", "passed": check_passed, "detail": detail})
                    if check_passed: passed += 1
                    else: failed += 1 if required else 0
                    
                elif rule_type == "text_contains":
                    after_text = after_evidence.get("text", "")
                    expected_text = expected.get("text")
                    check_passed = expected_text and expected_text in after_text
                    detail = "Text contains expected" if check_passed else f"Text does not contain '{expected_text}'"
                    results.append({"name": "text_contains", "passed": check_passed, "detail": detail})
                    if check_passed: passed += 1
                    else: failed += 1 if required else 0
                    
                elif rule_type == "element_present":
                    after_elements = after_evidence.get("elements", [])
                    selector = expected.get("selector")
                    check_passed = selector and any(e.get("selector") == selector for e in after_elements)
                    detail = "Element found" if check_passed else "Element not found"
                    results.append({"name": "element_present", "passed": check_passed, "detail": detail})
                    if check_passed: passed += 1
                    else: failed += 1 if required else 0
                    
                elif rule_type == "url_changed":
                    before_url = before_evidence.get("url", "")
                    after_url = after_evidence.get("url", "")
                    check_passed = before_url != after_url
                    detail = "URL changed" if check_passed else "URL did not change"
                    results.append({"name": "url_changed", "passed": check_passed, "detail": detail})
                    if check_passed: passed += 1
                    else: failed += 1 if required else 0
                    
                elif rule_type == "page_digest_changed":
                    before_digest = before_evidence.get("page_digest", "")
                    after_digest = after_evidence.get("page_digest", "")
                    check_passed = before_digest != after_digest
                    detail = "Page digest changed" if check_passed else "Page digest did not change"
                    results.append({"name": "page_digest_changed", "passed": check_passed, "detail": detail})
                    if check_passed: passed += 1
                    else: failed += 1 if required else 0
                    
                elif rule_type == "scroll_position_changed":
                    before_scroll = before_evidence.get("scroll_y", 0)
                    after_scroll = after_evidence.get("scroll_y", 0)
                    check_passed = before_scroll != after_scroll
                    detail = "Scroll position changed" if check_passed else "Scroll position did not change"
                    results.append({"name": "scroll_position_changed", "passed": check_passed, "detail": detail})
                    if check_passed: passed += 1
                    else: failed += 1 if required else 0
                    
                elif rule_type == "element_attribute":
                    after_elements = after_evidence.get("elements", [])
                    selector = expected.get("selector")
                    attribute = expected.get("attribute")
                    value = expected.get("value")
                    
                    matching = [e for e in after_elements if e.get("selector") == selector]
                    if matching:
                        actual_value = matching[0].get(attribute)
                        if value is None:
                            check_passed = actual_value is not None
                            detail = f"Element has attribute '{attribute}'" if check_passed else f"Element does not have attribute '{attribute}'"
                        else:
                            check_passed = actual_value == value
                            detail = f"Element attribute matches" if check_passed else f"Element attribute mismatch: expected '{value}', got '{actual_value}'"
                    else:
                        check_passed = False
                        detail = "Element not found"
                    
                    results.append({"name": "element_attribute", "passed": check_passed, "detail": detail})
                    if check_passed: passed += 1
                    else: failed += 1 if required else 0
                    
                else:
                    results.append({"name": rule_type or "unknown", "passed": None, "detail": f"Unsupported rule type: {rule_type}"})
                    inconclusive += 1
                    
            except Exception as e:
                results.append({"name": rule_type or "unknown", "passed": False, "detail": f"Verification error: {str(e)}"})
                failed += 1 if required else 0
        
        # Determine overall status
        if failed > 0:
            status = "failed"
        elif passed > 0 and inconclusive == 0 and failed == 0:
            status = "verified"
        elif passed > 0 and inconclusive > 0:
            status = "inconclusive"
        elif passed == 0 and inconclusive > 0:
            status = "inconclusive"
        else:
            status = "inconclusive"
        
        return {
            "status": status,
            "passed": passed,
            "failed": failed,
            "inconclusive": inconclusive,
            "checks": results,
            "observed_at": utcnow().isoformat(),
        }

    # ─── QUEUE AND WAIT WITH VERIFICATION ─────────────────────────

    async def queue_and_wait(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        task_id: uuid.UUID | None,
        task_thread_id: str | None = None,
        command_type: str,
        payload: dict[str, Any],
        timeout_seconds: int = 95,
    ) -> dict[str, Any]:
        if command_type not in ALLOWED_COMMANDS:
            raise ValueError("This browser action is not supported by Onsite48 Bridge.")
        async with self.session_factory() as session:
            resolved_task_id = task_id or await self._task_id_from_external_thread(
                session, organization_id=organization_id, user_id=user_id, task_thread_id=task_thread_id
            )
            device = await self._select_bound_device(
                session, organization_id=organization_id, user_id=user_id, task_id=resolved_task_id
            )
            session_id = None
            try:
                session_id = uuid.UUID(str(payload.get("session_id"))) if payload.get("session_id") else None
            except (TypeError, ValueError):
                raise RuntimeError("The browser session identifier is invalid.")
            if session_id is not None:
                await self.bind_browser_session(
                    session_id=session_id,
                    organization_id=organization_id,
                    user_id=user_id,
                    device=device,
                    task_id=resolved_task_id,
                    capabilities=payload.get("session_capabilities") if isinstance(payload.get("session_capabilities"), list) else None,
                )
            session_mode = "task_workspace"
            if session_id is not None:
                browser_session = await session.scalar(select(BrowserSession).where(
                    BrowserSession.id == session_id,
                    BrowserSession.organization_id == organization_id,
                    BrowserSession.user_id == user_id,
                ))
                if browser_session is None:
                    raise RuntimeError("The requested browser session no longer exists.")
                session_mode = str(browser_session.mode or "task_workspace")
                safe_session_mode = session_mode if session_mode in {"task_workspace", "task_tab"} else "task_workspace"
                payload["session_mode"] = safe_session_mode
            if command_type not in set(device.granted_capabilities or []):
                raise RuntimeError("The paired browser has not granted permission for that operation.")

            task_workspace_session = session_id is not None
            selected_context = (
                self._recent_context(device)
                if command_type in PAGE_BOUND_COMMANDS and not task_workspace_session
                else None
            )
            if command_type in PAGE_BOUND_COMMANDS and selected_context is None:
                raise self._selection_error(device)

            trusted, expected_hostname = await self.may_run_without_prompt(
                organization_id=organization_id,
                user_id=user_id,
                task_id=resolved_task_id,
                command_type=command_type,
                payload=payload,
            )
            safe_payload = dict(payload)
            if expected_hostname:
                safe_payload["expected_hostname"] = expected_hostname
            if selected_context is not None:
                safe_payload["target_tab"] = {
                    "tab_id": selected_context["tab_id"],
                    "window_id": selected_context["window_id"],
                    "hostname": selected_context["hostname"],
                    "title": selected_context["title"],
                    "selected_at": selected_context["selected_at"],
                }
                safe_payload.setdefault("expected_hostname", selected_context["hostname"])

            # Extract verification rules from payload
            verification_rules = safe_payload.get("verification", [])

            # Check for existing command of same type - don't auto-merge, create new command
            # to avoid silently dropping payload differences (e.g., different click selector)
            existing = await session.scalar(select(BrowserBridgeCommand).where(
                BrowserBridgeCommand.task_id == resolved_task_id,
                BrowserBridgeCommand.device_id == device.id,
                BrowserBridgeCommand.command_type == command_type,
                BrowserBridgeCommand.status.in_(["pending", "claimed"]),
            ).order_by(BrowserBridgeCommand.created_at.desc()).limit(1)) if resolved_task_id else None
            if existing is not None and selected_context is not None:
                existing_target = (existing.payload or {}).get("target_tab")
                if existing_target != safe_payload["target_tab"]:
                    if existing.status == "claimed":
                        existing.status = "cancelled"
                        existing.cancel_reason = "The selected browser tab changed after this command was claimed. A new command was created."
                        existing.error = existing.cancel_reason
                        existing.claim_lease_expires_at = None
                        existing.lease_token = None
                        existing.lease_expires_at = None
                    else:
                        existing.status = "failed"
                        existing.error = "The selected browser tab changed before this approved command ran. Select/Sync the intended tab and resume the task."
                        existing.preflight_status = "selected_tab_mismatch"
                        existing.completed_at = utcnow()
                else:
                    # Same target tab, but different payload - don't merge, create new command
                    # to avoid silently dropping payload differences
                    pass
            
            command = None
            if command is None:
                session_id = None
                try:
                    session_id = uuid.UUID(str(safe_payload.get("session_id"))) if safe_payload.get("session_id") else None
                except (TypeError, ValueError):
                    session_id = None
                step_id = str(safe_payload.get("step_id") or "")
                sequence = int(safe_payload.get("session_sequence", 0) or 0)
                idempotency_key = f"{resolved_task_id or task_thread_id or 'unbound'}:{session_id or 'none'}:{step_id}:{sequence}:{command_type}"
                command = BrowserBridgeCommand(
                    device_id=device.id,
                    organization_id=organization_id,
                    user_id=user_id,
                    task_id=resolved_task_id,
                    session_id=session_id,
                    sequence=sequence or None,
                    idempotency_key=idempotency_key,
                    command_type=command_type,
                    payload=safe_payload,
                    status="pending",
                    preflight_status="waiting",
                    requires_approval=not trusted,
                )
                session.add(command)
            await session.commit()
            command_id = command.id
            command_envelope = extension_command_payload(command)
            device_id = device.id

        # WebSocket delivery is best effort; the persisted pending command and
        # existing long-poll endpoint remain the backward-compatible fallback.
        await bridge_transport_registry.ensure_command_queue(command_id)
        await bridge_transport_registry.send_command(device_id, command_envelope)

        deadline = asyncio.get_running_loop().time() + max(15, min(timeout_seconds, 120))
        before_evidence = None
        
        try:
            while asyncio.get_running_loop().time() < deadline:
                await bridge_transport_registry.wait_for_event(command_id, 0.75)
                async with self.session_factory() as session:
                    command = await session.get(BrowserBridgeCommand, command_id)
                    if command is None:
                        raise RuntimeError("The paired-browser command disappeared before completion.")
                    if command.status == "completed":
                        result = command.result or {}
                        evidence = command.evidence or {}
                        
                        # ─── RUN VERIFICATION ──────────────────────────
                        if verification_rules:
                            before_evidence = evidence.get("before") if evidence else None
                            after_evidence = evidence.get("after") if evidence else None
                            verification = await self.verify_bridge_action(
                                device=device,
                                command_id=command_id,
                                before_evidence=before_evidence or {},
                                after_evidence=after_evidence or {},
                                verification_rules=verification_rules,
                            )
                        else:
                            verification = command.verification or {"status": "inconclusive", "checks": []}
                        
                        return {
                            "command_id": str(command.id),
                            "device_id": str(command.device_id),
                            "result": result,
                            "evidence": evidence or {},
                            "verification": verification,
                        }
                    if command.status == "pending" and command.preflight_status == "site_permission_required":
                        raise BridgePermissionRequired(origin=command.preflight_origin, command_id=command.id)
                    if command.status == "pending" and command.preflight_status in SELECTION_FAILURE_CODES:
                        raise BridgeSelectionRequired(
                            code=command.preflight_status,
                            message=command.preflight_reason or "Select/Sync the intended browser tab, then resume the task.",
                            command_id=command.id,
                        )
                    if command.status in {"failed", "expired", "cancelled"}:
                        raise RuntimeError(command.error or "The paired browser could not complete the request.")
            async with self.session_factory() as session:
                command = await session.get(BrowserBridgeCommand, command_id)
                if command and command.status == "pending":
                    command.status = "expired"
                    command.error = "The paired browser did not respond within the Bridge response window. Keep the extension enabled and the browser open, then retry."
                    await session.commit()
            raise RuntimeError("The paired browser did not respond within two polling windows. Confirm that Onsite48 Bridge is paired, enabled, and the browser is open, then retry.")
        finally:
            await bridge_transport_registry.drop_command_queue(command_id)

    async def revoke_device(self, *, organization_id: uuid.UUID, user_id: uuid.UUID, device_id: uuid.UUID) -> None:
        async with self.session_factory() as session:
            device = await session.scalar(select(BrowserBridgeDevice).where(
                BrowserBridgeDevice.id == device_id,
                BrowserBridgeDevice.organization_id == organization_id,
                BrowserBridgeDevice.user_id == user_id,
            ))
            if device is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paired browser not found.")
            device.status = "revoked"
            device.revoked_at = utcnow()
            device.access_token_hash = None
            device.pairing_secret_hash = None
            device.is_preferred = False
            await session.commit()