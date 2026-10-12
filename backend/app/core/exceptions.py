"""Custom exceptions for the Aether agent loop."""

from typing import Any


class ToolConfirmationRequired(Exception):
    """Raised when an irreversible tool requires human confirmation before execution."""

    def __init__(
        self,
        tool_name: str,
        tool_args: dict,
        step_description: str,
        step_index: int,
        *,
        task_scoped_read_only_available: bool = False,
    ):
        self.tool_name = tool_name
        self.tool_args = tool_args
        self.step_description = step_description
        self.step_index = step_index
        self.task_scoped_read_only_available = bool(task_scoped_read_only_available)
        super().__init__(f"Confirmation required for irreversible tool: {tool_name}")


class UserInterventionRequired(Exception):
    """Raised when a target requires a user-controlled verification or handoff step."""

    def __init__(
        self,
        *,
        reason: str,
        url: str,
        title: str,
        step_description: str,
        step_index: int,
        handoff_options: list[str],
        handoff_surface: str = "synth_bridge",
    ):
        self.reason = reason
        self.url = url
        self.title = title
        self.step_description = step_description
        self.step_index = step_index
        self.handoff_options = handoff_options
        self.handoff_surface = handoff_surface if handoff_surface in {"agent_computer", "synth_bridge"} else "synth_bridge"
        super().__init__(f"User intervention required at {url}: {reason}")