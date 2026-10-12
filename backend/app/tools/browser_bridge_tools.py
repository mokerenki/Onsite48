"""Approval-gated agent tools for the user-owned Onsite48 Bridge extension."""

from __future__ import annotations

from typing import Any

from app.services.browser_bridge import BridgePermissionRequired, BridgeSelectionRequired, BrowserBridgeService
from app.services.execution_identity import get_execution_identity
from app.core.browser_runtime import get_command_context
from app.tools.registry import BaseTool, ToolSchema


class _BridgeTool(BaseTool):
    command_type: str
    _OPERATOR_HANDOFF_MARKERS = (
        "operator was stopped by the user",
        "operator is paused by the user",
        "user took over",
        "requires the user to complete",
        "captcha or anti-bot challenge",
        "selected browser tab",
        "select/sync the intended tab",
    )

    def __init__(self, bridge: BrowserBridgeService):
        self.bridge = bridge
        super().__init__()

    async def _queue(self, payload: dict[str, Any]) -> dict[str, Any]:
        identity = get_execution_identity()
        session_context = payload.pop("_browser_session", None) or get_command_context()
        if isinstance(session_context, dict):
            payload.update({
                "session_id": str(session_context.get("session_id")),
                "session_mode": str(session_context.get("mode") or "task_workspace"),
                "session_origins": list(session_context.get("origins") or []),
                "step_id": str(session_context.get("step_id")),
                "session_sequence": int(session_context.get("sequence", 0) or 0),
                "session_capabilities": list(session_context.get("capabilities") or []),
            })
        try:
            response = await self.bridge.queue_and_wait(
                organization_id=identity.organization_id,
                user_id=identity.user_id,
                # The service resolves this external thread identity to the
                # owned TaskThread UUID before it creates a Bridge command.
                task_id=None,
                task_thread_id=getattr(identity, "task_thread_id", None),
                command_type=self.command_type,
                payload=payload,
            )
            nested = response.get("result") if isinstance(response, dict) else None
            if isinstance(nested, dict) and isinstance(nested.get("user_intervention"), dict):
                return {**response, "user_intervention": nested["user_intervention"], "url": nested.get("url", ""), "title": nested.get("title", "Local browser action paused")}
            return response
        except BridgePermissionRequired as exc:
            return {
                "user_intervention": {
                    "reason": str(exc),
                    "handoff_surface": "synth_bridge",
                    "permission_origin": exc.origin,
                    "command_id": str(exc.command_id),
                    "actions": ["Open Bridge popup & approve site", "Resume task", "Cancel"],
                },
                "url": exc.origin,
                "title": "One-time site permission required",
            }
        except BridgeSelectionRequired as exc:
            return {
                "user_intervention": {
                    "reason": str(exc),
                    "handoff_surface": "synth_bridge",
                    "selection_code": exc.code,
                    "command_id": str(exc.command_id) if exc.command_id else None,
                    "actions": ["Focus the page & Resume", "Cancel"],
                },
                "url": "",
                    "title": "Focused browser page required",
            }
        except RuntimeError as exc:
            message = str(exc)
            if any(marker in message.lower() for marker in self._OPERATOR_HANDOFF_MARKERS):
                return {
                    "user_intervention": {
                        "reason": message if "selected browser tab" in message.lower() or "select/sync" in message.lower() else "You paused or took over the local browser operator.",
                    },
                    "url": "",
                    "title": "Local browser operator paused",
                }
            raise

    async def may_execute_without_confirmation(self, payload: dict[str, Any]) -> bool:
        identity = get_execution_identity()
        allowed, _hostname = await self.bridge.may_run_without_prompt(
            organization_id=identity.organization_id,
            user_id=identity.user_id,
            task_id=None,
            command_type=self.command_type,
            payload=payload,
        )
        return allowed


class BridgeReadActiveTabTool(_BridgeTool):
    command_type = "read_active_tab"

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="bridge_read_active_tab",
            description=(
                "Read a structured observation from the user's paired local browser: meaningful page text, metadata, "
                "visible semantic/interactive elements, page fingerprints, and accessibility fallback when DOM text is sparse. "
                "In active-tab operator mode (default), uses the focused public tab without requiring Sync. "
                "This is read-only; the user’s explicit paired-tab request authorizes the observation."
            ),
            parameters={},
            irreversible=False,
            runtime="bridge",
            idempotency="read_only",
            approval_required=False,
            timeout_seconds=100,
            max_attempts=2,
        )

    async def execute(self, **_: Any) -> dict[str, Any]:
        return await self._queue({})


class BridgeObserveSurfaceTool(_BridgeTool):
    command_type = "observe_surface"

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="bridge_observe_surface",
            description=(
                "Read a structured, currently rendered browser surface such as a notifications panel, inbox, feed, or table "
                "from the user's paired tab. Returns semantic entries, page state, and bounded evidence; it never clicks, types, "
                "opens a panel, or sends anything. If the requested surface is closed or not rendered, report that clearly and "
                "ask for the user to open it rather than guessing."
            ),
            parameters={
                "surface": {"type": "string", "enum": ["notifications", "inbox", "feed", "table", "page"]},
                "max_items": {"type": "integer", "minimum": 1, "maximum": 50},
                "max_scrolls": {"type": "integer", "minimum": 0, "maximum": 5},
            },
            required=["surface"],
            irreversible=False,
            runtime="bridge",
            idempotency="read_only",
            approval_required=False,
            timeout_seconds=100,
            max_attempts=2,
        )

    async def execute(self, surface: str, max_items: int = 20, max_scrolls: int = 2, **_: Any) -> dict[str, Any]:
        return await self._queue({
            "surface": surface,
            "max_items": max(1, min(int(max_items or 20), 50)),
            "max_scrolls": max(0, min(int(max_scrolls or 0), 5)),
        })


class BridgeInspectActiveTabTool(_BridgeTool):
    command_type = "inspect_active_tab"

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="bridge_inspect_active_tab",
            description="Inspect visible controls on the active paired-browser tab. This is a read-only observation of the user-selected tab.",
            parameters={},
            irreversible=False,
            runtime="bridge",
            idempotency="read_only",
            approval_required=False,
            timeout_seconds=100,
            max_attempts=2,
        )

    async def execute(self, **_: Any) -> dict[str, Any]:
        return await self._queue({})


class BridgeScrollActiveTabTool(_BridgeTool):
    command_type = "scroll_active_tab"

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="bridge_scroll_active_tab",
            description="Scroll the active paired-browser tab. It never clicks, types, or submits anything.",
            parameters={"direction": {"type": "string", "enum": ["up", "down"]}, "amount": {"type": "integer"}},
            required=["direction"],
            irreversible=False,
            runtime="bridge",
            idempotency="read_only",
            approval_required=False,
            timeout_seconds=100,
            max_attempts=2,
        )

    async def execute(self, direction: str, amount: int | None = None, **_: Any) -> dict[str, Any]:
        return await self._queue({"direction": direction, "amount": amount})


class BridgeNavigateActiveTabTool(_BridgeTool):
    command_type = "navigate_active_tab"

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="bridge_navigate_active_tab",
            description="Navigate the active paired-browser tab to a public URL. A trusted-site scope can bypass the prompt only when navigation stays on that exact hostname; cross-site navigation always requires approval.",
            parameters={"url": {"type": "string"}},
            required=["url"],
            irreversible=True,
            runtime="bridge",
            idempotency="read_only",
            approval_required=True,
            timeout_seconds=100,
            max_attempts=2,
        )

    async def execute(self, url: str, **_: Any) -> dict[str, Any]:
        return await self._queue({"url": url})


class BridgeListTabsTool(_BridgeTool):
    command_type = "list_tabs"

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="bridge_list_tabs",
            description="List public website tabs in the user's current browser window. Always requires explicit approval because tab titles and sites may be sensitive.",
            parameters={},
            irreversible=True,
        )

    async def execute(self, **_: Any) -> dict[str, Any]:
        return await self._queue({})


class BridgeOpenTabTool(_BridgeTool):
    command_type = "open_tab"

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="bridge_open_tab",
            description="Open a specified public URL in a new local browser tab. Always requires explicit approval; opening a tab does not grant permission to act inside it.",
            parameters={"url": {"type": "string"}},
            required=["url"],
            irreversible=True,
        )

    async def execute(self, url: str, **_: Any) -> dict[str, Any]:
        return await self._queue({"url": url})


class BridgeActivateTabTool(_BridgeTool):
    command_type = "activate_tab"

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="bridge_activate_tab",
            description="Activate one user-approved public browser tab by identifier. Always requires explicit approval because changing tabs can expose unrelated signed-in content.",
            parameters={"tab_id": {"type": "integer"}},
            required=["tab_id"],
            irreversible=True,
        )

    async def execute(self, tab_id: int, **_: Any) -> dict[str, Any]:
        return await self._queue({"tab_id": tab_id})


class BridgeCaptureActiveTabTool(_BridgeTool):
    command_type = "capture_active_tab"

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="bridge_capture_active_tab",
            description="Capture the visible viewport of the active paired-browser tab for user-approved visual grounding. Screenshots can expose visible personal information and always require approval.",
            parameters={},
            irreversible=True,
        )

    async def execute(self, **_: Any) -> dict[str, Any]:
        return await self._queue({})


class BridgeSnapshotActiveTabTool(_BridgeTool):
    command_type = "snapshot_active_tab"

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="bridge_snapshot_active_tab",
            description="Capture a bounded live snapshot of the visible paired-browser tab with state digest and optional screenshot.",
            parameters={"include_screenshot": {"type": "boolean"}},
            irreversible=False,
            runtime="bridge",
            idempotency="read_only",
            approval_required=True,
            timeout_seconds=100,
            max_attempts=2,
        )

    async def execute(self, include_screenshot: bool = True, **_: Any) -> dict[str, Any]:
        return await self._queue({"include_screenshot": bool(include_screenshot)})


class BridgeDownloadActiveTabTool(_BridgeTool):
    command_type = "download_active_tab"

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="bridge_download_active_tab",
            description="Download a user-approved file from the visible paired-browser tab and return bounded download metadata.",
            parameters={"selector": {"type": "string"}, "url": {"type": "string"}},
            required=[], irreversible=True, runtime="bridge", idempotency="non_idempotent_write",
            approval_required=True, timeout_seconds=100, max_attempts=1,
        )

    async def execute(self, selector: str | None = None, url: str | None = None, **_: Any) -> dict[str, Any]:
        return await self._queue({"selector": selector, "url": url})


class BridgeUploadActiveTabTool(_BridgeTool):
    command_type = "upload_active_tab"

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="bridge_upload_active_tab",
            description="Upload a file explicitly staged by the user in the Onsite48 Bridge popup into a visible file input.",
            parameters={"selector": {"type": "string"}, "file_name": {"type": "string"}},
            required=["file_name"], irreversible=True, runtime="bridge", idempotency="non_idempotent_write",
            approval_required=True, timeout_seconds=100, max_attempts=1,
        )

    async def execute(self, file_name: str, selector: str | None = None, **_: Any) -> dict[str, Any]:
        return await self._queue({"selector": selector, "file_name": file_name})


class BridgeClickActiveTabTool(_BridgeTool):
    command_type = "click_active_tab"

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="bridge_click_active_tab",
            description="Click one visible link or ordinary button in the active paired-browser tab after explicit approval. The extension rejects form submission, password, CAPTCHA, payment, and security controls.",
            parameters={"selector": {"type": "string"}},
            required=["selector"],
            irreversible=True,
            runtime="bridge",
            idempotency="non_idempotent_write",
            approval_required=True,
            timeout_seconds=100,
            max_attempts=1,
        )

    async def execute(self, selector: str, **_: Any) -> dict[str, Any]:
        return await self._queue({"selector": selector})


class BridgeFillActiveTabTool(_BridgeTool):
    command_type = "fill_active_tab"

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="bridge_fill_active_tab",
            description="Type approved text into one ordinary text field in the active paired-browser tab. It never types into password, OTP, CAPTCHA, payment, or security fields and never submits a form.",
            parameters={"selector": {"type": "string"}, "value": {"type": "string"}},
            required=["selector", "value"],
            irreversible=True,
            runtime="bridge",
            idempotency="non_idempotent_write",
            approval_required=True,
            timeout_seconds=100,
            max_attempts=1,
        )

    async def execute(self, selector: str, value: str, **_: Any) -> dict[str, Any]:
        return await self._queue({"selector": selector, "value": value})



class BridgeLocateTool(_BridgeTool):
    """Ground one visible selected-tab target through the shared visual service."""

    command_type = "capture_active_tab"

    def __init__(self, bridge: BrowserBridgeService, visual_grounding: Any):
        self.visual_grounding = visual_grounding
        super().__init__(bridge)

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="bridge_locate",
            description="Capture the selected local-browser viewport through command-scoped CDP and use the shared visual grounding service to locate one visible target. It does not click or type, but screenshots require approval.",
            parameters={"description": {"type": "string", "description": "Concise description of the visible target to locate."}},
            required=["description"],
            irreversible=True,
            runtime="bridge",
            idempotency="read_only",
            approval_required=True,
            timeout_seconds=100,
            max_attempts=2,
        )

    async def execute(self, description: str, **_: Any) -> dict[str, Any]:
        captured = await self._queue({"cdp": True})
        result = captured.get("result") if isinstance(captured, dict) else None
        screenshot = result.get("screenshot_data_url") if isinstance(result, dict) else None
        if not isinstance(screenshot, str) or "," not in screenshot:
            raise RuntimeError("The selected browser tab did not provide a fresh screenshot for visual grounding.")
        grounded = await self.visual_grounding.locate(
            screenshot_base64=screenshot.split(",", 1)[1],
            description=description,
        )
        return {
            **grounded,
            "source": "bridge_cdp_screenshot",
            "command_id": captured.get("command_id") if isinstance(captured, dict) else None,
        }



class BridgeOpenTaskTabsTool(_BridgeTool):
    command_type = "open_task_tabs"

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="bridge_open_task_tabs",
            description="Open one to three specified public URLs as tabs owned by the current Synth task. This always requires exact approval. The extension may group the created tabs and records them so only this task can later close them.",
            parameters={"urls": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3}},
            required=["urls"],
            irreversible=True,
            runtime="bridge",
            idempotency="non_idempotent_write",
            approval_required=True,
            timeout_seconds=100,
            max_attempts=1,
        )

    async def execute(self, urls: list[str], **_: Any) -> dict[str, Any]:
        return await self._queue({"urls": urls})


class BridgeCloseTaskTabsTool(_BridgeTool):
    command_type = "close_task_tabs"

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="bridge_close_task_tabs",
            description="Close only tabs recorded by Onsite48 Bridge as opened for the current task. This always requires exact approval and cannot close arbitrary user tabs.",
            parameters={},
            irreversible=True,
            runtime="bridge",
            idempotency="non_idempotent_write",
            approval_required=True,
            timeout_seconds=100,
            max_attempts=1,
        )

    async def execute(self, **_: Any) -> dict[str, Any]:
        return await self._queue({})


class BridgeControlMediaTool(_BridgeTool):
    command_type = "control_media"

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="bridge_control_media",
            description="Pause, play, or toggle the HTML5 video/audio element in the focused paired browser tab. Uses direct media APIs and returns before/after state evidence; it does not click arbitrary page controls.",
            parameters={"action": {"type": "string", "enum": ["pause", "play", "toggle"]}},
            required=["action"],
            irreversible=True,
            runtime="bridge",
            idempotency="idempotent_write",
            approval_required=True,
            timeout_seconds=30,
            max_attempts=1,
        )

    async def execute(self, action: str, **_: Any) -> dict[str, Any]:
        return await self._queue({"action": action})