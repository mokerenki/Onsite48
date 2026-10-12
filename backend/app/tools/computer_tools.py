"""Agent tools for the isolated per-task browser computer."""

from __future__ import annotations

from typing import Any

from app.services.agent_computer import AgentComputerManager
from app.services.execution_identity import get_execution_identity
from app.tools.registry import BaseTool, ToolSchema


class ComputerNavigateTool(BaseTool):
    def __init__(self, computer: AgentComputerManager):
        self.computer = computer
        super().__init__()

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="computer_navigate",
            description="Open a public web page in the task's isolated browser computer. Local and private network URLs are blocked.",
            parameters={"url": {"type": "string", "description": "A complete public http or https URL."}},
            required=["url"],
            idempotency="read_only",
            approval_required=False,
        )

    async def execute(self, url: str, **_: Any) -> dict[str, Any]:
        return await self.computer.navigate(get_execution_identity().task_thread_id, url)


class ComputerBackTool(BaseTool):
    def __init__(self, computer: AgentComputerManager):
        self.computer = computer
        super().__init__()

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="computer_back",
            description="Return to the prior public page in the isolated browser history. This does not submit data or change the external service.",
            parameters={},
            idempotency="read_only",
            approval_required=False,
        )

    async def execute(self, **_: Any) -> dict[str, Any]:
        return await self.computer.go_back(get_execution_identity().task_thread_id)


class ComputerScrollTool(BaseTool):
    def __init__(self, computer: AgentComputerManager):
        self.computer = computer
        super().__init__()

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="computer_scroll",
            description="Scroll the current isolated browser page up or down to inspect content outside the initial viewport.",
            parameters={
                "direction": {"type": "string", "enum": ["up", "down"]},
                "amount": {"type": "integer", "description": "Optional scroll distance in pixels (50-10000)."},
            },
            required=["direction"],
            idempotency="read_only",
            approval_required=False,
        )

    async def execute(self, direction: str, amount: int | None = None, **_: Any) -> dict[str, Any]:
        return await self.computer.scroll(get_execution_identity().task_thread_id, direction, amount)


class ComputerInspectTool(BaseTool):
    def __init__(self, computer: AgentComputerManager):
        self.computer = computer
        super().__init__()

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="computer_inspect",
            description="List visible links, buttons, and input controls on the current isolated browser page, together with its scroll position.",
            parameters={},
        )

    async def execute(self, **_: Any) -> dict[str, Any]:
        return await self.computer.inspect(get_execution_identity().task_thread_id)


class ComputerClickTool(BaseTool):
    def __init__(self, computer: AgentComputerManager):
        self.computer = computer
        super().__init__()

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="computer_click",
            description="Click a specific element in the isolated browser. This may trigger an external action and always requires user approval.",
            parameters={"selector": {"type": "string", "description": "A selector returned by computer_inspect."}},
            required=["selector"],
            irreversible=True,
        )

    async def execute(self, selector: str, **_: Any) -> dict[str, Any]:
        return await self.computer.click(get_execution_identity().task_thread_id, selector)


class ComputerFillTool(BaseTool):
    def __init__(self, computer: AgentComputerManager):
        self.computer = computer
        super().__init__()

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="computer_fill",
            description="Fill a form field in the isolated browser. It always requires user approval and must not be used for credentials or payment information.",
            parameters={"selector": {"type": "string"}, "value": {"type": "string"}},
            required=["selector", "value"],
            irreversible=True,
        )

    async def execute(self, selector: str, value: str, **_: Any) -> dict[str, Any]:
        return await self.computer.fill(get_execution_identity().task_thread_id, selector, value)


class ComputerReadPageTool(BaseTool):
    def __init__(self, computer: AgentComputerManager):
        self.computer = computer
        super().__init__()

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="computer_read_page",
            description=(
                "Read a bounded section of text from the current isolated browser page. "
                "Use offset from a previous result to continue reading long pages. "
                "Pass 'selector' to read only a specific region (e.g. a product list or "
                "article body) instead of the whole page, which is cheaper and cleaner "
                "for a targeted extraction; if the selector matches nothing, the full "
                "page is returned instead with a warning. Hidden or invisible text "
                "(used for prompt injection) is stripped before this text is returned; "
                "check 'hidden_content_detected' in the result if you need to know "
                "whether anything was removed."
            ),
            parameters={
                "offset": {"type": "integer", "description": "Zero-based text offset; defaults to 0."},
                "limit": {"type": "integer", "description": "Maximum characters to read; defaults to 20000, maximum 30000."},
                "selector": {"type": "string", "description": "Optional CSS selector to narrow the read to a specific region of the page."},
            },
        )

    async def execute(self, offset: int = 0, limit: int = 20_000, selector: str | None = None, **_: Any) -> dict[str, Any]:
        return await self.computer.read_page(
            get_execution_identity().task_thread_id, offset=offset, limit=limit, selector=selector
        )


class ComputerLocateTool(BaseTool):
    def __init__(self, computer: AgentComputerManager):
        self.computer = computer
        super().__init__()

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="computer_locate",
            description="Use a fresh viewport screenshot and Kimi vision grounding to locate a visible control when computer_inspect cannot provide a reliable selector. It is read-only and does not click.",
            parameters={"description": {"type": "string", "description": "Concise description of the visible element to locate."}},
            required=["description"],
            idempotency="read_only",
            approval_required=False,
        )

    async def execute(self, description: str, **_: Any) -> dict[str, Any]:
        return await self.computer.locate_element(get_execution_identity().task_thread_id, description)


class ComputerClickAtTool(BaseTool):
    def __init__(self, computer: AgentComputerManager):
        self.computer = computer
        super().__init__()

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="computer_click_at",
            description="Click at viewport coordinates previously returned by computer_locate. This can trigger an external action and always requires user approval.",
            parameters={
                "x": {"type": "number", "description": "Horizontal coordinate, normalized by default."},
                "y": {"type": "number", "description": "Vertical coordinate, normalized by default."},
                "normalized": {"type": "boolean", "description": "True for 0.0-1.0 viewport coordinates; defaults to true."},
            },
            required=["x", "y"],
            irreversible=True,
        )

    async def execute(self, x: float, y: float, normalized: bool = True, **_: Any) -> dict[str, Any]:
        return await self.computer.click_at(get_execution_identity().task_thread_id, x=x, y=y, normalized=normalized)


class ComputerTypeAtTool(BaseTool):
    def __init__(self, computer: AgentComputerManager):
        self.computer = computer
        super().__init__()

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="computer_type_at",
            description="Click at previously grounded viewport coordinates and type text. This always requires user approval and must not be used for credentials or payment information.",
            parameters={
                "x": {"type": "number", "description": "Horizontal coordinate, normalized by default."},
                "y": {"type": "number", "description": "Vertical coordinate, normalized by default."},
                "text": {"type": "string", "description": "Text to type, maximum 10000 characters."},
                "normalized": {"type": "boolean", "description": "True for 0.0-1.0 viewport coordinates; defaults to true."},
            },
            required=["x", "y", "text"],
            irreversible=True,
        )

    async def execute(self, x: float, y: float, text: str, normalized: bool = True, **_: Any) -> dict[str, Any]:
        return await self.computer.type_at(get_execution_identity().task_thread_id, x=x, y=y, text=text, normalized=normalized)


class ComputerPressKeyTool(BaseTool):
    def __init__(self, computer: AgentComputerManager):
        self.computer = computer
        super().__init__()

    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="computer_press_key",
            description="Press a bounded keyboard key or modifier combination in the isolated browser. This may submit a form or trigger navigation and always requires user approval.",
            parameters={"key": {"type": "string", "description": "Supported key such as Enter, Tab, Escape, ArrowDown, or Control+A."}},
            required=["key"],
            irreversible=True,
        )

    async def execute(self, key: str, **_: Any) -> dict[str, Any]:
        return await self.computer.press_key(get_execution_identity().task_thread_id, key)