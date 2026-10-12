"""A durable task queue on the task table itself.

REST task acceptance dispatched through `fastapi.BackgroundTasks`, which run in
the process that accepted the request, after the response is sent. That is not a
queue. If the process is restarted, redeployed or evicted between accepting a
task and running it, the work is gone: the row stays `queued` with a lease
nobody holds, and the sweeper can only fail it. The user asked for something and
is told it failed for a reason that has nothing to do with their request.

The fix is to make the database the queue. A task row is the message, `queued`
plus a lapsed-or-absent lease is "available", and a consumer claims work with
`SELECT ... FOR UPDATE SKIP LOCKED` and marks it `running` with a fresh lease in
the same transaction. That is the standard Postgres queue, and it gets three
things right that BackgroundTasks cannot:

* **Two consumers never take the same task.** `SKIP LOCKED` makes the second one
  move on to other work rather than block on the first, which is also what stops
  a crashed consumer from wedging the queue for its lease duration.
* **The claim and the state change are one transaction.** A crash between them
  is not representable, so a task is never running with nobody holding it and
  never available with a consumer believing it owns it.
* **An accepted task survives its process.** The row is the work; nothing about
  it lives in a Python object's memory.

The sweeper stops failing recoverable work: a lapsed `running` task is handed
back to the queue while it has attempts left, and only failed once it does not.
That is the difference between a task that survives a deploy and a task that
does not.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import case, select

from app.models import TaskStatus, TaskThread
from app.core.platform_metrics import metrics

#: How many times a task may be dispatched before losing its worker is treated
#: as a failure rather than a restart.
#:
#: Three is enough to ride out a rolling deploy or a crash loop, and low enough
#: that a task which genuinely cannot complete stops occupying a consumer slot.
#: Beyond this the work is failed with a reason that says so, rather than being
#: retried indefinitely and never reaching a terminal state.
MAX_DISPATCH_ATTEMPTS = 3

#: A claimed task keeps this long without progress before the sweeper takes it
#: back. Long enough that a slow tool call does not lose its lease, short enough
#: that a dead consumer is replaced while the user is still watching.
CLAIM_LEASE_SECONDS = 900


@dataclass(frozen=True)
class ClaimedTask:
    id: uuid.UUID
    external_thread_id: str
    user_id: uuid.UUID
    organization_id: uuid.UUID
    prompt: str
    attempt: int
    lease_token: str


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


async def claim_next(
    session_factory: Any,
    *,
    limit: int = 1,
    now: datetime | None = None,
) -> list[ClaimedTask]:
    """Take up to `limit` available tasks, marking them running and leased.

    "Available" is `queued` with no lease or a lapsed one. A live lease means
    another consumer holds the row, and `SKIP LOCKED` keeps this consumer moving
    to other work instead of waiting on a transaction that may never commit.

    The whole batch is one transaction. If it cannot be taken as a unit, none of
    it is, so a consumer never reports work it did not claim.
    """
    moment = _now(now)
    claimed: list[ClaimedTask] = []

    async with session_factory() as session:
        async with session.begin():
            candidates: Iterable[TaskThread] = (
                await session.scalars(
                    select(TaskThread)
                    .where(
                        TaskThread.status == TaskStatus.QUEUED,
                        # A null lease is a task that has never been claimed.
                        # A lapsed lease is one whose claimer went away. Both
                        # are available; a live one is not.
                        (TaskThread.lease_expires_at.is_(None))
                        | (TaskThread.lease_expires_at <= moment),
                    )
                    .order_by(
                        case(
                            (TaskThread.priority == "urgent", 0),
                            (TaskThread.priority == "high", 1),
                            (TaskThread.priority == "medium", 2),
                            else_=3,
                        ),
                        TaskThread.created_at.asc(),
                    )
                    .with_for_update(skip_locked=True)
                    .limit(max(1, limit))
                )
            ).all()

            for task in candidates:
                # The claim is a lease, not a status change. Status stays QUEUED
                # until execution actually begins, so a consumer that dies
                # between claiming and starting leaves a task the sweeper is
                # already allowed to recover. Flipping to RUNNING here would put
                # a claimed-but-not-started task outside the sweeper's scope
                # entirely, because RUNNING is deliberately never reaped: a slow
                # step can outlast any lease set without a per-step heartbeat.
                task.lease_expires_at = moment + timedelta(seconds=CLAIM_LEASE_SECONDS)
                task.attempt_count = int(task.attempt_count or 0) + 1
                task.lease_token = uuid.uuid4().hex
                claimed.append(
                    ClaimedTask(
                        id=task.id,
                        external_thread_id=task.external_thread_id,
                        user_id=task.user_id,
                        organization_id=task.organization_id,
                        prompt=task.prompt,
                        attempt=task.attempt_count,
                        lease_token=task.lease_token,
                    )
                )

    if claimed:
        metrics.inc("onsite48_queue_claims_total", len(claimed))
        metrics.set("onsite48_queue_claim_batch_size", len(claimed))
    return claimed


async def mark_started(
    session_factory: Any,
    task_id: Any,
    *,
    lease_token: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Record that a claimed task has begun executing.

    Called once the executor actually starts, so the window between claiming and
    running -- where a crash is cheap to detect and safe to retry -- stays as
    small as possible. Returns False if the claim was lost, so a consumer that
    was too slow does not start work another consumer has already taken.
    """
    from sqlalchemy import update

    moment = _now(now)
    async with session_factory() as session:
        result = await session.execute(
            update(TaskThread)
            .where(
                TaskThread.id == task_id,
                TaskThread.status == TaskStatus.QUEUED,
                # Only a live lease: if it lapsed, the sweeper may already have
                # handed this task to somebody else.
                TaskThread.lease_expires_at > moment,
                *((TaskThread.lease_token == lease_token,) if lease_token else ()),
            )
            .values(
                status=TaskStatus.RUNNING,
                started_at=moment,
                updated_at=moment,
            )
        )
        await session.commit()
        return result.rowcount > 0


async def heartbeat_claim(session_factory: Any, task_id: Any, *, lease_token: str | None = None, now: datetime | None = None, seconds: int = CLAIM_LEASE_SECONDS) -> bool:
    """Extend a worker lease only while the task is still running."""
    from sqlalchemy import update
    moment = _now(now)
    async with session_factory() as session:
        result = await session.execute(
            update(TaskThread)
            .where(
                TaskThread.id == task_id,
                TaskThread.status == TaskStatus.RUNNING,
                *((TaskThread.lease_token == lease_token,) if lease_token else ()),
            )
            .values(lease_expires_at=moment + timedelta(seconds=seconds), updated_at=moment)
        )
        await session.commit()
        return result.rowcount > 0


async def is_cancelled(session_factory: Any, task_id: Any) -> bool:
    """Read the authoritative cancellation signal before starting a step."""
    async with session_factory() as session:
        task_status = await session.scalar(select(TaskThread.status).where(TaskThread.id == task_id))
        return task_status == TaskStatus.CANCELLED


async def release_for_retry(
    session: Any,
    task: TaskThread,
    *,
    now: datetime | None = None,
    reason: str,
) -> bool:
    """Hand a lapsed task back to the queue. False when it has run out of attempts.

    Called by the sweeper for a task whose consumer died. Requeueing is correct
    only while the work can still be expected to succeed somewhere else; a task
    that keeps losing its consumer is failed instead, with a reason that says so
    rather than the generic orphaned message.
    """
    moment = _now(now)
    attempts = int(task.attempt_count or 0)
    if attempts >= MAX_DISPATCH_ATTEMPTS:
        metrics.inc("onsite48_queue_dead_letter_total")
        return False

    task.status = TaskStatus.QUEUED
    # Cleared so the consumer that picks it up is visible as available. Leaving
    # the dead lease in place would make the row look claimed forever.
    task.lease_expires_at = None
    task.lease_token = None
    task.completed_at = None
    task.final_answer = None

    metadata = dict(task.execution_metadata or {})
    history = list(metadata.get("dispatch_retries") or [])
    history.append(
        {"at": moment.isoformat(), "attempt": attempts, "reason": reason[:200]}
    )
    # Bounded so a long crash loop cannot grow the row without limit.
    metadata["dispatch_retries"] = history[-10:]
    metadata["requeued_at"] = moment.isoformat()
    task.execution_metadata = metadata
    metrics.inc("onsite48_queue_retries_total")
    return True


def should_retry(attempt_count: int | None) -> bool:
    return int(attempt_count or 0) < MAX_DISPATCH_ATTEMPTS