"""Authoritative task lifecycle and replay-safe event semantics.

The transport may deliver events more than once or out of order after reconnects.
This module keeps the allowed lifecycle transitions explicit and gives persistence
layers a single place to validate state changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.models import TaskStatus


class LifecyclePhase(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_USER = "waiting_for_user"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_FINAL = "failed_final"
    CANCELLED = "cancelled"


_STATUS_TO_PHASE = {
    TaskStatus.QUEUED: LifecyclePhase.CREATED,
    TaskStatus.RUNNING: LifecyclePhase.EXECUTING,
    TaskStatus.PAUSED: LifecyclePhase.WAITING_FOR_USER,
    TaskStatus.WAITING_FOR_APPROVAL: LifecyclePhase.WAITING_FOR_APPROVAL,
    TaskStatus.COMPLETED: LifecyclePhase.COMPLETED,
    TaskStatus.PARTIAL: LifecyclePhase.PARTIAL,
    TaskStatus.FAILED: LifecyclePhase.FAILED_FINAL,
    TaskStatus.CANCELLED: LifecyclePhase.CANCELLED,
}

_ALLOWED: dict[LifecyclePhase, frozenset[LifecyclePhase]] = {
    LifecyclePhase.CREATED: frozenset({LifecyclePhase.CREATED, LifecyclePhase.PLANNING, LifecyclePhase.EXECUTING, LifecyclePhase.CANCELLED}),
    LifecyclePhase.PLANNING: frozenset({LifecyclePhase.PLANNING, LifecyclePhase.EXECUTING, LifecyclePhase.WAITING_FOR_APPROVAL, LifecyclePhase.FAILED_RETRYABLE, LifecyclePhase.FAILED_FINAL, LifecyclePhase.CANCELLED}),
    LifecyclePhase.EXECUTING: frozenset({LifecyclePhase.EXECUTING, LifecyclePhase.WAITING_FOR_APPROVAL, LifecyclePhase.WAITING_FOR_USER, LifecyclePhase.VERIFYING, LifecyclePhase.COMPLETED, LifecyclePhase.PARTIAL, LifecyclePhase.FAILED_RETRYABLE, LifecyclePhase.FAILED_FINAL, LifecyclePhase.CANCELLED}),
    LifecyclePhase.WAITING_FOR_APPROVAL: frozenset({LifecyclePhase.WAITING_FOR_APPROVAL, LifecyclePhase.EXECUTING, LifecyclePhase.CANCELLED}),
    LifecyclePhase.WAITING_FOR_USER: frozenset({LifecyclePhase.WAITING_FOR_USER, LifecyclePhase.EXECUTING, LifecyclePhase.CANCELLED}),
    LifecyclePhase.VERIFYING: frozenset({LifecyclePhase.VERIFYING, LifecyclePhase.COMPLETED, LifecyclePhase.PARTIAL, LifecyclePhase.FAILED_RETRYABLE, LifecyclePhase.FAILED_FINAL, LifecyclePhase.WAITING_FOR_USER}),
    LifecyclePhase.COMPLETED: frozenset({LifecyclePhase.COMPLETED}),
    LifecyclePhase.PARTIAL: frozenset({LifecyclePhase.PARTIAL}),
    LifecyclePhase.FAILED_RETRYABLE: frozenset({LifecyclePhase.FAILED_RETRYABLE, LifecyclePhase.PLANNING, LifecyclePhase.EXECUTING, LifecyclePhase.CANCELLED}),
    LifecyclePhase.FAILED_FINAL: frozenset({LifecyclePhase.FAILED_FINAL}),
    LifecyclePhase.CANCELLED: frozenset({LifecyclePhase.CANCELLED}),
}


@dataclass(frozen=True)
class Transition:
    previous: LifecyclePhase
    current: LifecyclePhase
    event_type: str
    accepted: bool
    reason: str = ""


def phase_for_status(status: TaskStatus | str | None) -> LifecyclePhase:
    try:
        return _STATUS_TO_PHASE[TaskStatus(status or TaskStatus.QUEUED)]
    except (KeyError, ValueError):
        return LifecyclePhase.CREATED


def phase_for_event(event_type: str, *, failure_retryable: bool = False, status: str | None = None) -> LifecyclePhase | None:
    mapping = {
        "task_start": LifecyclePhase.EXECUTING,
        "planning_started": LifecyclePhase.PLANNING,
        "planner_output": LifecyclePhase.EXECUTING,
        "needs_confirmation": LifecyclePhase.WAITING_FOR_APPROVAL,
        "needs_user_intervention": LifecyclePhase.WAITING_FOR_USER,
        "needs_clarification": LifecyclePhase.WAITING_FOR_USER,
        "user_intervention_required": LifecyclePhase.WAITING_FOR_USER,
        "task_paused": LifecyclePhase.WAITING_FOR_USER,
        "task_resumed": LifecyclePhase.EXECUTING,
        "task_complete": LifecyclePhase.PARTIAL if status == TaskStatus.PARTIAL.value else LifecyclePhase.COMPLETED,
        "task_error": LifecyclePhase.FAILED_RETRYABLE if failure_retryable else LifecyclePhase.FAILED_FINAL,
        "error": LifecyclePhase.FAILED_FINAL,
        "task_cancelled": LifecyclePhase.CANCELLED,
    }
    return mapping.get(event_type)


def validate_transition(previous: TaskStatus | str | None, target: LifecyclePhase, event_type: str) -> Transition:
    before = phase_for_status(previous)
    if target in _ALLOWED[before]:
        return Transition(before, target, event_type, True)
    return Transition(before, target, event_type, False, f"invalid lifecycle transition {before.value} -> {target.value} from {event_type}")


def event_identity(event: dict[str, Any]) -> str | None:
    """Return a stable identity supplied by producers, if available."""
    for key in ("event_id", "event_uuid", "idempotency_key"):
        value = event.get(key)
        if value:
            return str(value)
    return None


def should_apply_event(metadata: dict[str, Any], event: dict[str, Any]) -> bool:
    """Deduplicate replayed non-transient events using bounded metadata."""
    identity = event_identity(event)
    if not identity:
        return True
    seen = [str(item) for item in metadata.get("seen_event_ids", []) if item]
    if identity in seen:
        return False
    metadata["seen_event_ids"] = (seen + [identity])[-500:]
    return True


def task_status_for_phase(phase: LifecyclePhase) -> TaskStatus | None:
    if phase in {LifecyclePhase.CREATED, LifecyclePhase.PLANNING}:
        return TaskStatus.QUEUED
    if phase in {LifecyclePhase.EXECUTING, LifecyclePhase.VERIFYING}:
        return TaskStatus.RUNNING
    if phase == LifecyclePhase.WAITING_FOR_APPROVAL:
        return TaskStatus.WAITING_FOR_APPROVAL
    if phase == LifecyclePhase.WAITING_FOR_USER:
        return TaskStatus.PAUSED
    if phase in {LifecyclePhase.FAILED_RETRYABLE, LifecyclePhase.FAILED_FINAL}:
        return TaskStatus.FAILED
    if phase == LifecyclePhase.COMPLETED:
        return TaskStatus.COMPLETED
    if phase == LifecyclePhase.PARTIAL:
        return TaskStatus.PARTIAL
    if phase == LifecyclePhase.CANCELLED:
        return TaskStatus.CANCELLED
    return None


def lifecycle_snapshot(status: TaskStatus | str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    phase = phase_for_status(status)
    return {"phase": phase.value, "status": str(status), "checkpoint": (metadata or {}).get("checkpoint"), "last_event_id": (metadata or {}).get("last_event_id")}


__all__ = ["LifecyclePhase", "Transition", "event_identity", "lifecycle_snapshot", "phase_for_event", "phase_for_status", "should_apply_event", "task_status_for_phase", "validate_transition"]


# Keep Any imported in the public module for callers building JSON event envelopes.
_ = Any