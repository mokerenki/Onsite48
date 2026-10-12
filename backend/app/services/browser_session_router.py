"""Unified BrowserSession routing: Local (extension) vs Cloud (Playwright).

Agent loop uses a single entrypoint; it does not care Local vs Cloud.
Policy is explicit — no regex on user goal text.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

logger = structlog.get_logger("onsite48.browser_session_router")


@dataclass
class BrowserBackendDecision:
    backend: str  # "local" | "cloud" | "ask_user"
    reason: str
    requires_user_browser: bool = False
    extension_connected: bool = False
    extension_authorized: bool = False


def select_backend(
    *,
    requires_user_browser: bool = False,
    my_browser_preferred: bool = False,
    extension_connected: bool = False,
    extension_authorized: bool = False,
) -> BrowserBackendDecision:
    """Explicit Local vs Cloud policy.

    - requires_user_browser OR My Browser preferred → Local if connected+authorized
    - If local required but not connected/authorized → ask_user (never silent Cloud for auth-sensitive work)
    - Otherwise Cloud
    """
    wants_local = bool(requires_user_browser or my_browser_preferred)
    if wants_local:
        if extension_connected and extension_authorized:
            return BrowserBackendDecision(
                backend="local",
                reason="requires_user_browser_or_preferred_and_ready",
                requires_user_browser=requires_user_browser,
                extension_connected=True,
                extension_authorized=True,
            )
        return BrowserBackendDecision(
            backend="ask_user",
            reason="local_required_but_not_connected_or_authorized",
            requires_user_browser=requires_user_browser,
            extension_connected=extension_connected,
            extension_authorized=extension_authorized,
        )
    return BrowserBackendDecision(
        backend="cloud",
        reason="default_cloud",
        requires_user_browser=False,
        extension_connected=extension_connected,
        extension_authorized=extension_authorized,
    )


class BrowserSession(abc.ABC):
    """Unified session API used by the runtime loop."""

    @abc.abstractmethod
    async def observe(self, *, include_screenshot: bool = False, tab_id: int | None = None) -> dict[str, Any]:
        ...

    @abc.abstractmethod
    async def act(self, *, action: str, **payload: Any) -> dict[str, Any]:
        ...

    @abc.abstractmethod
    async def request_takeover(self, *, reason: str) -> dict[str, Any]:
        ...

    @abc.abstractmethod
    async def resume(self) -> dict[str, Any]:
        """Resume after takeover. Must imply a fresh observe before the next act."""
        ...

    @abc.abstractmethod
    async def stop(self, *, reason: str = "stopped") -> dict[str, Any]:
        ...

    @property
    def is_paused_for_challenge(self) -> bool:
        return False

    @property
    def consumes_step_budget(self) -> bool:
        """While paused for takeover/challenge, do not burn the same wall-time step budget."""
        return not self.is_paused_for_challenge


class LocalBridgeSession(BrowserSession):
    """Execution via the Onsite48 Browser extension (task-owned tab group)."""

    def __init__(self, bridge, *, organization_id, user_id, task_thread_id=None, session_id=None):
        self.bridge = bridge
        self.organization_id = organization_id
        self.user_id = user_id
        self.task_thread_id = task_thread_id
        self.session_id = session_id
        self._paused = False
        self._needs_fresh_observe = False
        self._workspace_created = False

    async def _ensure_workspace(self):
        """Create task workspace if not already created."""
        if self._workspace_created or not self.session_id:
            return
        try:
            await self._queue("create_task_workspace", {
                "session_id": self.session_id,
                "task_id": None,
                "title": f"Onsite48 — Task {self.task_thread_id or ''}".strip(),
                "start_url": "about:blank",
            })
            self._workspace_created = True
        except Exception as e:
            # Workspace may already exist (idempotent), or other transient error
            logger.warning("workspace_creation_failed", session_id=self.session_id, error=str(e)[:200])
            self._workspace_created = True  # Don't retry endlessly

    async def _queue(self, command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        # Ensure workspace exists for workspace-bound commands
        if command_type in {"observe", "inspect", "locate", "screenshot", "read_page",
                           "navigate", "click", "type", "scroll", "back", "forward", "reload",
                           "double_click", "right_click", "hover", "insert_text", "press_key",
                           "key_down", "key_up", "hotkey", "scroll_to", "drag", "drag_and_drop",
                           "select_option", "check", "uncheck", "focus", "wait",
                           "wait_for_url", "wait_for_text", "wait_for_selector", "wait_for_navigation",
                           "wait_for_network_idle", "wait_for_dom_stable", "wait_for_download", "wait_for_dialog",
                           "control_media", "download", "upload", "open_task_tab", "close_task_tab",
                           "list_task_tabs", "takeover", "resume", "stop"}:
            await self._ensure_workspace()
        return await self.bridge.queue_and_wait(
            organization_id=self.organization_id,
            user_id=self.user_id,
            task_id=None,
            task_thread_id=self.task_thread_id,
            command_type=command_type,
            payload=payload or {},
        )

    async def observe(self, *, include_screenshot: bool = False, tab_id: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"include_screenshot": include_screenshot}
        if tab_id is not None:
            payload["tab_id"] = tab_id
        result = await self._queue("observe", payload)
        self._needs_fresh_observe = False
        return result

    async def act(self, *, action: str, **payload: Any) -> dict[str, Any]:
        if self._needs_fresh_observe:
            await self.observe(include_screenshot=False)
        body = dict(payload)
        body["action"] = action
        # Map common actions to extension command types (new task-owned forms)
        command_type = {
            "navigate": "navigate",
            "click": "click",
            "type": "type",
            "scroll": "scroll",
            "open_tab": "open_task_tab",
            "close_tab": "close_task_tab",
            "back": "back",
            "forward": "forward",
            "reload": "reload",
            "double_click": "double_click",
            "right_click": "right_click",
            "hover": "hover",
            "insert_text": "insert_text",
            "press_key": "press_key",
            "key_down": "key_down",
            "key_up": "key_up",
            "hotkey": "hotkey",
            "scroll_to": "scroll_to",
            "drag": "drag",
            "drag_and_drop": "drag_and_drop",
            "select_option": "select_option",
            "check": "check",
            "uncheck": "uncheck",
            "focus": "focus",
            "wait": "wait",
            "wait_for_url": "wait_for_url",
            "wait_for_text": "wait_for_text",
            "wait_for_selector": "wait_for_selector",
            "wait_for_navigation": "wait_for_navigation",
            "wait_for_network_idle": "wait_for_network_idle",
            "wait_for_dom_stable": "wait_for_dom_stable",
            "wait_for_download": "wait_for_download",
            "wait_for_dialog": "wait_for_dialog",
            "control_media": "control_media",
            "download": "download",
            "upload": "upload",
        }.get(action, action)
        return await self._queue(command_type, body)

    async def request_takeover(self, *, reason: str) -> dict[str, Any]:
        self._paused = True
        result = await self._queue("takeover", {"reason": reason})
        return {
            **(result if isinstance(result, dict) else {"result": result}),
            "paused": True,
            "is_paused_for_challenge": True,
            "consumes_step_budget": False,
            "event": "browser.takeover",
        }

    async def resume(self) -> dict[str, Any]:
        self._paused = False
        self._needs_fresh_observe = True  # mandatory fresh observe before next act
        result = await self._queue("resume", {})
        # Contract: always return a fresh observation on resume
        obs = await self.observe(include_screenshot=False)
        return {
            "resumed": True,
            "event": "browser.running_local",
            "observation": obs,
            "resume_result": result,
        }

    async def stop(self, *, reason: str = "stopped") -> dict[str, Any]:
        self._paused = False
        result = await self._queue("stop", {"reason": reason})
        # Prefer close_task_workspace for hard kill of tab group
        try:
            await self._queue("close_task_workspace", {"reason": reason})
        except Exception:
            pass
        return {
            **(result if isinstance(result, dict) else {"result": result}),
            "event": "browser.stopped",
            "terminal": True,
        }

    @property
    def is_paused_for_challenge(self) -> bool:
        return self._paused


class CloudPlaywrightSession(BrowserSession):
    """Execution via isolated cloud Playwright (Agent Computer / browser runtime)."""

    def __init__(self, computer=None, operator=None, *, thread_id: str = "default"):
        self.computer = computer
        self.operator = operator
        self.thread_id = thread_id
        self._paused = False
        self._needs_fresh_observe = False

    async def observe(self, *, include_screenshot: bool = False, tab_id: int | None = None) -> dict[str, Any]:
        if self.operator is not None:
            obs = await self.operator.observe(include_screenshot=include_screenshot)
            self._needs_fresh_observe = False
            return {"ok": True, "action": "observe", "observation": obs.to_dict() if hasattr(obs, "to_dict") else obs}
        if self.computer is not None and hasattr(self.computer, "observe"):
            self._needs_fresh_observe = False
            return await self.computer.observe(include_screenshot=include_screenshot)
        return {"ok": False, "error": "Cloud browser operator unavailable"}

    async def act(self, *, action: str, **payload: Any) -> dict[str, Any]:
        if self._needs_fresh_observe:
            await self.observe(include_screenshot=False)
        if self.operator is not None:
            from app.runtime.browser.protocol import ActionRequest, ActionTarget
            target = None
            if payload.get("element_ref") or payload.get("observation_id") or payload.get("coordinates"):
                target = ActionTarget(
                    observation_id=payload.get("observation_id"),
                    element_ref=payload.get("element_ref"),
                    coordinates=payload.get("coordinates"),
                )
            req = ActionRequest(
                action=action,  # type: ignore[arg-type]
                target=target,
                text=payload.get("text"),
                key=payload.get("key"),
                url=payload.get("url"),
                file_path=payload.get("file_path"),
                option=payload.get("option"),
                timeout_ms=payload.get("timeout_ms"),
            )
            if action == "observe":
                return await self.observe(include_screenshot=bool(payload.get("include_screenshot")))
            receipt = await self.operator.act(req)
            return receipt.to_dict() if hasattr(receipt, "to_dict") else dict(receipt)
        if self.computer is not None and hasattr(self.computer, "act"):
            return await self.computer.act(action=action, **payload)
        return {"ok": False, "error": "Cloud browser operator unavailable"}

    async def request_takeover(self, *, reason: str) -> dict[str, Any]:
        self._paused = True
        return {
            "ok": True,
            "paused": True,
            "is_paused_for_challenge": True,
            "consumes_step_budget": False,
            "reason": reason,
            "event": "browser.takeover",
        }

    async def resume(self) -> dict[str, Any]:
        self._paused = False
        self._needs_fresh_observe = True
        obs = await self.observe(include_screenshot=False)
        return {"resumed": True, "event": "browser.running_local", "observation": obs}

    async def stop(self, *, reason: str = "stopped") -> dict[str, Any]:
        self._paused = False
        if self.computer is not None and hasattr(self.computer, "stop"):
            await self.computer.stop()
        return {"ok": True, "event": "browser.stopped", "terminal": True, "reason": reason}

    @property
    def is_paused_for_challenge(self) -> bool:
        return self._paused


@dataclass
class BrowserSessionRouter:
    """Resolves LocalBridgeSession | CloudPlaywrightSession for the agent loop."""

    bridge: Any = None
    computer_manager: Any = None
    browser_session_service: Any = None
    # Optional flags supplied per resolve call or from task context
    default_requires_user_browser: bool = False
    default_my_browser_preferred: bool = False

    async def extension_status(self, *, organization_id, user_id) -> tuple[bool, bool]:
        """Return (connected, authorized) for the user's preferred device."""
        if self.bridge is None:
            return False, False
        try:
            # Best-effort: list devices / preferred
            if hasattr(self.bridge, "list_connected_devices"):
                devices = await self.bridge.list_connected_devices(
                    organization_id=organization_id, user_id=user_id
                )
                for d in devices or []:
                    status = getattr(d, "status", None) or (d.get("status") if isinstance(d, dict) else None)
                    session_state = getattr(d, "session_state", None) or (
                        d.get("session_state") if isinstance(d, dict) else None
                    )
                    if status == "connected":
                        authorized = session_state in ("ACTIVE", "AUTHORIZED", "authorized", "active")
                        return True, bool(authorized)
            return False, False
        except Exception as exc:
            logger.warning("extension_status_failed", error=str(exc)[:200])
            return False, False

    async def resolve(
        self,
        identity,
        *,
        requires_user_browser: bool | None = None,
        my_browser_preferred: bool | None = None,
    ) -> BrowserSession | None:
        requires = (
            self.default_requires_user_browser
            if requires_user_browser is None
            else requires_user_browser
        )
        preferred = (
            self.default_my_browser_preferred
            if my_browser_preferred is None
            else my_browser_preferred
        )
        org_id = getattr(identity, "organization_id", None)
        user_id = getattr(identity, "user_id", None)
        thread_id = getattr(identity, "task_thread_id", None)

        connected, authorized = False, False
        if org_id and user_id:
            connected, authorized = await self.extension_status(
                organization_id=org_id, user_id=user_id
            )

        decision = select_backend(
            requires_user_browser=requires,
            my_browser_preferred=preferred,
            extension_connected=connected,
            extension_authorized=authorized,
        )
        logger.info(
            "browser_backend_selected",
            backend=decision.backend,
            reason=decision.reason,
            connected=connected,
            authorized=authorized,
        )

        if decision.backend == "local" and self.bridge is not None:
            return LocalBridgeSession(
                self.bridge,
                organization_id=org_id,
                user_id=user_id,
                task_thread_id=thread_id,
            )
        if decision.backend == "ask_user":
            # Caller should surface connect/authorize CTA; do not silent-fall to Cloud
            return None
        # Cloud
        operator = None
        computer = None
        if self.computer_manager is not None:
            try:
                computer = self.computer_manager
            except Exception:
                computer = None
        return CloudPlaywrightSession(computer=computer, operator=operator, thread_id=str(thread_id or "default"))
