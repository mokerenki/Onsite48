"""Pure durable task-thread state helpers shared by API and tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from app.models import TaskStatus, TaskThread


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def status_from_event(event: dict[str, Any]) -> Optional[TaskStatus]:
    event_type = event.get("type")
    if event_type == "task_start":
        return TaskStatus.RUNNING
    if event_type == "needs_confirmation":
        return TaskStatus.WAITING_FOR_APPROVAL
    if event_type == "needs_user_intervention":
        return TaskStatus.PAUSED
    if event_type == "needs_clarification":
        return TaskStatus.PAUSED
    if event_type == "task_complete":
        raw = event.get("status")
        if raw == "cancelled":
            return TaskStatus.CANCELLED
        if raw == "partial":
            return TaskStatus.PARTIAL
        return TaskStatus.COMPLETED
    # Protocol errors are informational. Only a terminal execution error is
    # allowed to change the durable task status to FAILED.
    if event_type == "task_error":
        return TaskStatus.FAILED
    return None


def conversation_for(task: TaskThread) -> list[dict[str, str]]:
    metadata = task.execution_metadata or {}
    conversation = metadata.get("conversation", [])
    return conversation if isinstance(conversation, list) else []


def append_conversation_turn(task: TaskThread, role: str, content: str) -> None:
    content = content.strip()
    if not content:
        return
    metadata = dict(task.execution_metadata or {})
    conversation = conversation_for(task)
    if conversation and conversation[-1].get("role") == role and conversation[-1].get("content") == content:
        return
    conversation.append({"turn_id": str(uuid.uuid4()), "role": role, "content": content, "at": utcnow().isoformat()})
    # Keep enough history for working conversations while bounding stored size.
    metadata["conversation"] = conversation[-100:]
    metadata["turn_count"] = sum(1 for turn in metadata["conversation"] if turn.get("role") == "user")
    task.execution_metadata = metadata


def ensure_final_answer_in_conversation(task: TaskThread, fallback_answer: str | None = None) -> bool:
    """Backfill a historical transcript from the durable task answer when needed.

    Earlier releases persisted a task-level final_answer but did not always add
    the matching assistant turn to execution_metadata. This normalizes those
    records lazily when a user reopens the task, without overwriting a complete
    conversation.
    """
    answer = (task.final_answer or fallback_answer or "").strip()
    if not answer:
        return False
    conversation = conversation_for(task)
    if not conversation and task.prompt:
        append_conversation_turn(task, "user", task.prompt)
        conversation = conversation_for(task)
    if conversation and conversation[-1].get("role") == "assistant":
        return False
    append_conversation_turn(task, "assistant", answer)
    return True


class StatusConflict(RuntimeError):
    """The task was no longer in a status this action may transition from.

    Raised instead of silently overwriting: the caller must re-read the task and
    decide what to tell the user, because the state it based its decision on is
    already stale.
    """


async def transition_status(
    session: Any,
    task_id: Any,
    *,
    expected: "frozenset[TaskStatus] | set[TaskStatus] | tuple[TaskStatus, ...]",
    target: TaskStatus,
    values: Optional[dict[str, Any]] = None,
) -> TaskThread:
    """Move a task between statuses only if it is still where the caller saw it.

    The pattern this replaces was: open a session, read the task, decide the
    new status is allowed, then open a *second* session, re-read the row, and
    assign the new status unconditionally. Between the check and the write
    another connection can move the row -- a cancel, a second resume, the orphan
    sweeper -- and the unconditional write silently wins. The user then watches
    a cancelled task start running again.

    A single conditional UPDATE closes the window: the status predicate is
    evaluated by the database against the row it is actually locking, so a
    concurrent transition either loses the race here or wins, never both.

    Returns the refreshed row, or raises :class:`StatusConflict`.
    """
    from sqlalchemy import update

    statement = (
        update(TaskThread)
        .where(TaskThread.id == task_id, TaskThread.status.in_(tuple(expected)))
        .values(status=target, **(values or {}))
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(statement)
    if result.rowcount == 0:
        await session.rollback()
        raise StatusConflict(
            f"task {task_id} is no longer in {[str(s) for s in expected]}"
        )
    await session.commit()

    task = await session.get(TaskThread, task_id)
    if task is None:  # pragma: no cover - the UPDATE matched a row moments ago
        raise StatusConflict(f"task {task_id} disappeared during the transition")
    return task


async def fail_task_terminal(
    session: Any,
    task: TaskThread,
    *,
    error_code: str,
    message: str,
    retryable: bool = False,
    trace_id: str | None = None,
    thread_id: str | None = None,
    error_event_id: str | None = None,
) -> bool:
    """Transition a task to a terminal failed state and emit exactly one error event.

    This is the single cleanup path for an outer WebSocket / realtime exception.
    It is idempotent: a task already in a terminal state (completed, failed,
    cancelled, partial) is left untouched and ``False`` is returned, so a second
    cleanup attempt cannot paint a second failure over a completed run.

    The terminal event carries an identity derived from ``error_event_id`` when
    the caller has one, so ``record_event`` can deduplicate a replayed cleanup
    without needing a separate guard table. When no id is supplied a fresh UUID
    is minted, which is correct for a first failure and still unique.
    """
    from sqlalchemy import func, select

    from app.models import TaskEvent

    if task.status in {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.PARTIAL,
    }:
        return False

    now = utcnow()
    event_id = str(error_event_id or uuid.uuid4())
    event = {
        "type": "task_error",
        "event_id": event_id,
        "task_id": str(task.id),
        "thread_id": thread_id or str(task.external_thread_id),
        "error_code": error_code,
        "message": message,
        "error": message,
        "retryable": retryable,
        "trace_id": trace_id or "",
        "timestamp": now.isoformat(),
    }

    task.status = TaskStatus.FAILED
    task.lease_expires_at = None
    task.lease_token = None
    task.completed_at = now
    task.final_answer = message
    metadata = dict(task.execution_metadata or {})
    metadata["task_error"] = {
        "error_code": error_code,
        "message": message,
        "retryable": retryable,
        "terminal": True,
        "trace_id": trace_id or "",
    }
    task.execution_metadata = metadata

    last_sequence = await session.scalar(
        select(func.max(TaskEvent.sequence)).where(TaskEvent.task_id == task.id)
    )
    event_record = TaskEvent(
        task_id=task.id,
        sequence=int(last_sequence or 0) + 1,
        event_type="task_error",
        payload=event,
        trace_id=trace_id or None,
    )
    session.add(event_record)
    await session.flush()
    return True