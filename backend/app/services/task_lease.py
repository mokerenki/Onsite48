"""Leases for accepted task work, and recovery of work that was lost.

REST task acceptance dispatched through FastAPI ``BackgroundTasks``, which run in
the accepting process after the response has been sent. Nothing about that is
durable: if the process restarts, the coroutine is simply gone. The task row is
left at ``queued`` and stays there indefinitely, so the user watches a task that
never progresses, with no terminal state and nothing for an operator to alert on.

A lease on the row is what makes the loss detectable. Accepted work is stamped
with an expiry and the expiry is refreshed as the task reports progress, so a
lapsed lease is evidence that a process went away.

What the sweeper does about it changed once the database became a real queue
(``app/services/task_queue.py``). It originally failed every lapsed task, which
was honest but lossy: a deploy between accept and run turned accepted work into
a visible failure, and a single restart was enough to do it. The multi-replica
concern that motivated "fail rather than re-dispatch" is now handled properly,
by ``SELECT ... FOR UPDATE SKIP LOCKED`` at claim time, so a lapsed task is
requeued while it has attempts left and failed only when it does not. A task
that keeps losing its consumer now fails with a reason that says so, which is
both terminal and actionable.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import TaskEvent, TaskStatus, TaskThread, utcnow
from app.services.task_queue import MAX_DISPATCH_ATTEMPTS, release_for_retry, should_retry

logger = structlog.get_logger("onsite48.services.task_lease")

# Generous enough that a slow but healthy task never trips it, short enough that
# lost work is surfaced in minutes rather than at the next deploy.
TASK_LEASE_SECONDS = 900

ORPHANED_ERROR_CODE = "orphaned_task"
ORPHANED_MESSAGE = (
    "This task was accepted but the process running it stopped before it began. "
    "Nothing was changed. Please run it again."
)

#: Used when the task has already been requeued the maximum number of times.
#:
#: Distinct from ORPHANED_MESSAGE because it tells the user something different
#: and actionable: the request itself is fine, but the platform repeatedly lost
#: the worker holding it. Reading the generic orphaned message, a user re-runs
#: and loses it again.
EXHAUSTED_ERROR_CODE = "dispatch_attempts_exhausted"
EXHAUSTED_MESSAGE = (
    "This task lost the process running it repeatedly and was stopped after "
    f"{MAX_DISPATCH_ATTEMPTS} attempts. Nothing was left half-finished. This is "
    "usually temporary -- please try again in a few minutes."
)

# Only a task that never started is provably orphaned. A `running` task is
# deliberately excluded: a single long step (a wide research sweep, a slow
# provider) can outrun any lease we can set without a heartbeat that fires
# *during* that step, so reaping running work would kill healthy tasks. Making
# that safe needs a heartbeat inside the executor loop, which is tracked
# separately.
REAPABLE_STATUSES = (TaskStatus.QUEUED,)


def lease_deadline(seconds: int = TASK_LEASE_SECONDS, *, now: datetime | None = None) -> datetime:
    return (now or utcnow()) + timedelta(seconds=seconds)


async def acquire_task_lease(
    session: AsyncSession,
    *,
    task_id: uuid.UUID,
    seconds: int = TASK_LEASE_SECONDS,
) -> None:
    """Stamp a lease when work is dispatched for a task."""
    task = await session.get(TaskThread, task_id)
    if task is not None and task.status == TaskStatus.QUEUED:
        task.lease_expires_at = lease_deadline(seconds)


def refresh_task_lease(task: TaskThread, *, seconds: int = TASK_LEASE_SECONDS) -> None:
    """Push the lease out. Called on every progress event, so a healthy task
    keeps moving its own deadline and a dead one does not."""
    if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        return
    task.lease_expires_at = lease_deadline(seconds)


def release_task_lease(task: TaskThread) -> bool:
    """Clear the lease and lease token for a task that is no longer running.

    Idempotent and safe to call on a task whose lease was already released or
    whose row has since been reaped by the sweeper. Returns whether a lease was
    actually present, so callers can distinguish "nothing to do" from "already
    terminal" without raising.
    """
    had_lease = bool(getattr(task, "lease_expires_at", None) or getattr(task, "lease_token", None))
    task.lease_expires_at = None
    task.lease_token = None
    return had_lease


async def release_task_lease_by_id(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> bool:
    """Release a lease by primary key, re-fetching the row first.

    The lease lives on the durable task row, so a cleanup path that only holds a
    stale in-memory object can race against the sweeper reaping the same task.
    Re-fetching under the advisory lock the sweeper also uses keeps the two
    writers serialised and makes this a no-op if the row moved on.
    """
    from sqlalchemy import update

    moment = now or utcnow()
    async with session_factory() as session:
        result = await session.execute(
            update(TaskThread)
            .where(
                TaskThread.id == task_id,
                TaskThread.lease_expires_at.is_not(None),
            )
            .values(lease_expires_at=None, lease_token=None, updated_at=moment)
        )
        await session.commit()
        return result.rowcount > 0


async def _fail_orphaned(
    session: AsyncSession,
    task: TaskThread,
    *,
    now: datetime,
    requeued: bool = False,
) -> dict[str, Any]:
    task.status = TaskStatus.FAILED
    task.lease_expires_at = None
    task.completed_at = now

    exhausted = not should_retry(task.attempt_count)
    message = ORPHANED_MESSAGE if requeued else (
        EXHAUSTED_MESSAGE if exhausted else ORPHANED_MESSAGE
    )
    task.final_answer = message

    metadata = dict(task.execution_metadata or {})
    metadata["task_error"] = {
        "error_code": EXHAUSTED_ERROR_CODE if exhausted else ORPHANED_ERROR_CODE,
        "message": message,
    }
    if exhausted:
        metadata["dead_lettered_at"] = now.isoformat()
        metadata["dead_letter_reason"] = "dispatch_attempts_exhausted"
    metadata["orphaned_at"] = now.isoformat()
    task.execution_metadata = metadata

    # The task row is already locked FOR UPDATE by the caller, so allocating the
    # next sequence here cannot race with another writer.
    last_sequence = await session.scalar(
        select(func.max(TaskEvent.sequence)).where(TaskEvent.task_id == task.id)
    )
    session.add(TaskEvent(
        task_id=task.id,
        sequence=int(last_sequence or 0) + 1,
        event_type="task_error",
        payload={
            "type": "task_error",
            "task_id": str(task.id),
            "thread_id": str(task.external_thread_id),
            "error_code": EXHAUSTED_ERROR_CODE if exhausted else ORPHANED_ERROR_CODE,
            "dead_lettered": exhausted,
            "message": message,
            "error": message,
            "timestamp": now.isoformat(),
        },
    ))
    return {
        "task_id": str(task.id),
        "thread_id": str(task.external_thread_id),
        "organization_id": str(task.organization_id),
        "user_id": str(task.user_id),
        "expired_at": task.updated_at.isoformat() if task.updated_at else None,
    }


async def sweep_orphaned_tasks(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Recover every accepted task whose dispatch lease has lapsed.

    A lapsed lease means a process went away holding the work. What happens next
    depends on how many times the task has been dispatched:

    * **Attempts remain** -- the task is handed back to the queue. The lease is
      cleared so the next consumer sees it as available, and the reason is
      recorded on the task. This is the case that used to lose work: a deploy
      between accept and run turned a perfectly good request into a failure the
      user had to make again.
    * **Attempts exhausted** -- the task is failed, with a reason that says it
      kept losing its consumer rather than the generic orphaned message. A task
      that can never finish should not occupy a consumer slot or sit in the
      queue forever.

    Returns one record per task recovered or failed, so the caller can alert on
    a non-empty result. Records carry ``requeued`` so a requeue is not mistaken
    for a failure by whatever consumes this.
    """
    moment = now or utcnow()
    recovered: list[dict[str, Any]] = []
    requeued: list[dict[str, Any]] = []
    async with session_factory() as session:
        async with session.begin():
            candidates: Iterable[TaskThread] = (await session.scalars(
                select(TaskThread)
                .where(
                    TaskThread.status.in_(REAPABLE_STATUSES),
                    TaskThread.lease_expires_at.is_not(None),
                    TaskThread.lease_expires_at <= moment,
                )
                .order_by(TaskThread.lease_expires_at.asc())
                .with_for_update(skip_locked=True)
                .limit(limit)
            )).all()
            for task in candidates:
                attempts = int(task.attempt_count or 0)
                if await release_for_retry(session, task, now=moment, reason="lease lapsed"):
                    requeued.append({
                        "task_id": str(task.id),
                        "thread_id": str(task.external_thread_id),
                        "organization_id": str(task.organization_id),
                        "user_id": str(task.user_id),
                        "attempts": attempts,
                        "requeued": True,
                    })
                    continue
                record = await _fail_orphaned(session, task, now=moment)
                record["attempts"] = attempts
                record["requeued"] = False
                recovered.append(record)

    if requeued:
        logger.warning(
            "task_lease.orphaned_tasks_requeued",
            count=len(requeued),
            task_ids=[item["task_id"] for item in requeued],
        )
    if recovered:
        logger.error(
            "task_lease.orphaned_tasks_failed",
            count=len(recovered),
            error_code=ORPHANED_ERROR_CODE,
            task_ids=[item["task_id"] for item in recovered],
        )
    return requeued + recovered