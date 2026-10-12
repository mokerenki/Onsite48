"""Pure preflight decisions for agent steps.

The service deliberately does not execute tools or mutate the database. Callers
persist the returned decision and then perform the corresponding transition.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Literal

Decision = Literal["run", "approval", "takeover", "replan", "stop"]


@dataclass(frozen=True)
class ExecutionDecision:
    decision: Decision
    reason: str
    approval_key: str | None = None
    required_capabilities: tuple[str, ...] = ()
    failure_class: str | None = None


def decide_preflight(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    tool_irreversible: bool,
    authorized: bool = True,
    session_status: str | None = None,
    capability: str | None = None,
    capability_granted: bool = True,
    approval_present: bool = False,
    schedule_allows: bool = False,
    approval_key: str | None = None,
) -> ExecutionDecision:
    """Return the only safe next decision for one step."""
    if tool_name.startswith("bridge_"):
        if session_status in {"authorizing", "paused", "takeover", "expired", "failed", "cancelled"} or not authorized:
            return ExecutionDecision("takeover", "Browser session authorization or user control is required.")
        if capability and not capability_granted:
            return ExecutionDecision("takeover", f"Browser capability '{capability}' has not been granted.", required_capabilities=(capability,))
    if tool_irreversible and not approval_present and not schedule_allows:
        return ExecutionDecision("approval", "An exact approval is required before this state-changing action.", approval_key=approval_key)
    return ExecutionDecision("run", "Preflight checks passed.")