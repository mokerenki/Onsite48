"""Canonical persisted execution states and transition vocabulary."""
from __future__ import annotations
from enum import StrEnum


class ExecutionState(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    VALIDATING = "validating"
    PREFLIGHT = "preflight"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_USER = "waiting_for_user"
    OBSERVING = "observing"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

TERMINAL_EXECUTION_STATES = frozenset({ExecutionState.COMPLETED, ExecutionState.FAILED, ExecutionState.CANCELLED})


def is_terminal(value: str | ExecutionState | None) -> bool:
    try:
        return ExecutionState(str(value)) in TERMINAL_EXECUTION_STATES
    except ValueError:
        return False