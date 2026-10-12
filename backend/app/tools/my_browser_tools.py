"""Collapsed My Browser agent-facing tools (protocol v3.1).

Model-visible surface is intentionally small:
  - browser_observe
  - browser_act
  - browser_takeover
  - browser_stop

Fine-grained COMMAND_TYPES remain extension-internal and are mapped here.
Do not re-expand the public tool zoo.
"""
from __future__ import annotations

from typing import Any, Optional

from app.tools.registry import BaseTool, ToolSchema


class _BridgeFacade:
    """Thin facade over BrowserBridgeService for the collapsed tools."""

    def __init__(self, bridge):
        self.bridge = bridge

    async def queue(self, *, identity, command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.bridge.queue_and_wait(
            organization_id=identity.organization_id,
            user_id=identity.user_id,
            task_id=None,
            task_thread_id=getattr(identity, "task_thread_id", None),
            command_type=command_type,
            payload=payload or {},
        )


def _identity():
    from app.services.execution_identity import get_execution_identity
    return get_execution_identity()


class BrowserObserveTool(BaseTool):
    """Observe the task-owned browser workspace (local or cloud via router)."""

    def __init__(self, bridge=None, session_router=None):
        self.bridge = bridge
        self.session_router = session_router
        super().__init__()

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="browser_observe",
            description=(
                "Observe the authorized task browser workspace. Returns structured page state "
                "(title, url, interactive elements, optional screenshot). Always observe before act. "
                "On resume after takeover, call this first. Screenshots are task-tab scoped only."
            ),
            parameters={
                "include_screenshot": {
                    "type": "boolean",
                    "description": "Include a task-scoped viewport screenshot when available.",
                },
                "tab_id": {
                    "type": "integer",
                    "description": "Optional task-owned tab id; defaults to primary workspace tab.",
                },
            },
            required=[],
            idempotency="read_only",
            approval_required=False,
        )

    async def execute(self, **kwargs):
        identity = _identity()
        if self.session_router is not None:
            session = await self.session_router.resolve(identity)
            if session is not None:
                return await session.observe(
                    include_screenshot=bool(kwargs.get("include_screenshot")),
                    tab_id=kwargs.get("tab_id"),
                )
        if self.bridge is None:
            return {"ok": False, "error": "No browser backend available"}
        facade = _BridgeFacade(self.bridge)
        payload = {
            "include_screenshot": bool(kwargs.get("include_screenshot", False)),
        }
        if kwargs.get("tab_id") is not None:
            payload["tab_id"] = int(kwargs["tab_id"])
        return await facade.queue(identity=identity, command_type="observe", payload=payload)


class BrowserActTool(BaseTool):
    """Perform one action in the task-owned browser workspace."""

    # Internal mapping: model action -> extension COMMAND_TYPE
    ACTION_MAP = {
        "navigate": "navigate",
        "click": "click",
        "type": "type",
        "insert_text": "insert_text",
        "press_key": "press_key",
        "scroll": "scroll",
        "select_option": "select_option",
        "hover": "hover",
        "wait": "wait",
        "wait_for_dom_stable": "wait_for_dom_stable",
        "wait_for_network_idle": "wait_for_network_idle",
        "wait_for_selector": "wait_for_selector",
        "download": "download",
        "upload": "upload",
        "back": "back",
        "forward": "forward",
        "reload": "reload",
        "open_tab": "open_task_tab",
        "close_tab": "close_task_tab",
    }

    def __init__(self, bridge=None, session_router=None):
        self.bridge = bridge
        self.session_router = session_router
        super().__init__()

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="browser_act",
            description=(
                "Act inside the authorized task browser workspace. Prefer element_ref from the "
                "latest observation. Supported actions: navigate, click, type, press_key, scroll, "
                "select_option, wait_for_dom_stable, wait_for_network_idle, download, upload, "
                "open_tab, close_tab, back, forward, reload. Do not guess coordinates when a ref exists."
            ),
            parameters={
                "action": {
                    "type": "string",
                    "description": "Action name (navigate, click, type, scroll, ...).",
                },
                "url": {"type": "string", "description": "Target URL for navigate."},
                "element_ref": {"type": "string", "description": "Element ref from observation."},
                "observation_id": {"type": "string", "description": "Observation id binding this act."},
                "text": {"type": "string", "description": "Text for type/insert_text."},
                "key": {"type": "string", "description": "Key for press_key."},
                "direction": {"type": "string", "description": "Scroll direction: up|down|left|right."},
                "amount": {"type": "number", "description": "Scroll amount."},
                "option": {"type": "string", "description": "Option label/value for select_option."},
                "file_path": {"type": "string", "description": "Path for upload."},
                "tab_id": {"type": "integer", "description": "Optional task-owned tab id."},
                "timeout_ms": {"type": "integer", "description": "Optional wait timeout."},
            },
            required=["action"],
        )

    async def execute(self, **kwargs):
        identity = _identity()
        action = str(kwargs.get("action") or "").strip().lower()
        command_type = self.ACTION_MAP.get(action, action)
        payload = {k: v for k, v in kwargs.items() if k != "action" and v is not None}
        payload["action"] = action

        if self.session_router is not None:
            session = await self.session_router.resolve(identity)
            if session is not None:
                return await session.act(action=action, **payload)

        if self.bridge is None:
            return {"ok": False, "error": "No browser backend available"}
        facade = _BridgeFacade(self.bridge)
        return await facade.queue(identity=identity, command_type=command_type, payload=payload)


class BrowserTakeoverTool(BaseTool):
    """Request human takeover (login / MFA / CAPTCHA / user control)."""

    def __init__(self, bridge=None, session_router=None):
        self.bridge = bridge
        self.session_router = session_router
        super().__init__()

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="browser_takeover",
            description=(
                "Request human takeover of the task browser workspace. Use for login walls, MFA, "
                "CAPTCHA, or any step that needs the user. Paused challenge time should not consume "
                "the same step wall-clock budget. After the user resumes, call browser_observe before the next act."
            ),
            parameters={
                "reason": {
                    "type": "string",
                    "description": "Clear reason shown to the user (e.g. 'Complete MFA on this page').",
                },
            },
            required=["reason"],
            idempotency="read_only",
            approval_required=False,
        )

    async def execute(self, **kwargs):
        identity = _identity()
        reason = str(kwargs.get("reason") or "User action required in the browser.")
        if self.session_router is not None:
            session = await self.session_router.resolve(identity)
            if session is not None:
                return await session.request_takeover(reason=reason)
        if self.bridge is None:
            return {"ok": False, "error": "No browser backend available"}
        facade = _BridgeFacade(self.bridge)
        return await facade.queue(
            identity=identity,
            command_type="takeover",
            payload={"reason": reason},
        )


class BrowserStopTool(BaseTool):
    """Hard-stop the task browser workspace."""

    def __init__(self, bridge=None, session_router=None):
        self.bridge = bridge
        self.session_router = session_router
        super().__init__()

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="browser_stop",
            description=(
                "Stop browser work for this task. Closes the task tab group / ends the session. "
                "Produces a terminal stop on the backend (no further commands)."
            ),
            parameters={
                "reason": {
                    "type": "string",
                    "description": "Optional stop reason.",
                },
            },
            required=[],
            irreversible=True,
        )

    async def execute(self, **kwargs):
        identity = _identity()
        reason = str(kwargs.get("reason") or "Stopped by agent or user.")
        if self.session_router is not None:
            session = await self.session_router.resolve(identity)
            if session is not None:
                return await session.stop(reason=reason)
        if self.bridge is None:
            return {"ok": False, "error": "No browser backend available"}
        facade = _BridgeFacade(self.bridge)
        return await facade.queue(
            identity=identity,
            command_type="stop",
            payload={"reason": reason},
        )


def register_my_browser_tools(registry, bridge, session_router=None) -> None:
    """Register only the small model-visible set."""
    registry.register(BrowserObserveTool(bridge, session_router))
    registry.register(BrowserActTool(bridge, session_router))
    registry.register(BrowserTakeoverTool(bridge, session_router))
    registry.register(BrowserStopTool(bridge, session_router))
