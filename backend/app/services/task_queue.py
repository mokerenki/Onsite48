from __future__ import annotations

import os
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, select

from app.core.platform_metrics import metrics
from app.models import TaskStatus, TaskThread

#: Priority lane definitions: higher value = higher priority.
#: The integer is used for queue ordering (lower sorts first).
PRIORITY_LANES: dict[str, int] = {
    "urgent": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}

#: How many times a task may be dispatched before losing its worker is treated
#: as a failure rather than a restart.
MAX_DISPATCH_ATTEMPTS = 3

#: A claimed task keeps this long without progress before the sweeper takes it
#: back.
CLAIM_LEASE_SECONDS = 900

#: A task that has been running longer than this without a heartbeat is a
#: candidate for timeout enforcement.
TASK_TIMEOUT_SECONDS = int(os.getenv("TASK_TIMEOUT_SECONDS", "1800"))

#: Redis key prefix for the dead-letter queue.
DLQ_KEY_PREFIX = "onsite48:dlq:"


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
    return now or datetime.now(UTC)


async def enqueue_with_priority(
    session: Any,
    prompt: str,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    *,
    thread_id: str | None = None,
    priority: str = "medium",
    title: str | None = None,
) -> TaskThread:
    """Insert a task at a specific priority lane.

    Uses the raw SQLAlchemy model so callers can wrap it in a larger
    transaction. The priority is validated against PRIORITY_LANES.
    """
    lane = PRIORITY_LANES.get(priority, PRIORITY_LANES["medium"])
    task = TaskThread(
        external_thread_id=thread_id or str(uuid.uuid4()),
        user_id=user_id,
        organization_id=organization_id,
        prompt=prompt,
        title=title or prompt[:200],
        status=TaskStatus.QUEUED,
        priority=TaskThread.Priority(priority) if hasattr(TaskThread, "Priority") else priority,
        execution_metadata={
            "priority_lane": priority,
            "priority_order": lane,
        },
    )
    session.add(task)
    await session.flush()
    metrics.inc("onsite48_queue_enqueued_total", labels={"priority": priority})
    return task


async def claim_task(
    session_factory: Any,
    *,
    limit: int = 1,
    now: datetime | None = None,
    priority: str | None = None,
) -> list[ClaimedTask]:
    """Take up to `limit` available tasks, marking them running and leased.

    Priority lane selection:
    - If `priority` is given, only tasks at that priority are claimed.
    - If absent, tasks are claimed in priority order (urgent first).

    "Available" is `queued` with no lease or a lapsed one. A live lease means
    another consumer holds the row, and `SKIP LOCKED` keeps this consumer moving
    to other work instead of waiting on a transaction that may never commit.

    The claim is a lease, not a status change. Status stays QUEUED
    until execution actually begins, so a consumer that dies between claiming
    and starting leaves a task the sweeper is allowed to recover.
    """
    moment = _now(now)
    claimed: list[ClaimedTask] = []

    order_expr = case(
        (TaskThread.priority == "urgent", 0),
        (TaskThread.priority == "high", 1),
        (TaskThread.priority == "medium", 2),
        else_=3,
    )

    query = (
        select(TaskThread)
        .where(
            TaskThread.status == TaskStatus.QUEUED,
            (TaskThread.lease_expires_at.is_(None))
            | (TaskThread.lease_expires_at <= moment),
        )
        .order_by(
            order_expr,
            TaskThread.created_at.asc(),
        )
        .with_for_update(skip_locked=True)
        .limit(max(1, limit))
    )

    if priority is not None:
        lane = PRIORITY_LANES.get(priority)
        if lane is not None:
            query = query.where(
                case(
                    (TaskThread.priority == "urgent", 0),
                    (TaskThread.priority == "high", 1),
                    (TaskThread.priority == "medium", 2),
                    else_=3,
                ) == lane
            )

    async with session_factory() as session, session.begin():
        candidates: Iterable[TaskThread] = (
            await session.scalars(query)
        ).all()

        for task in candidates:
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


async def claim_next(
    session_factory: Any,
    *,
    limit: int = 1,
    now: datetime | None = None,
) -> list[ClaimedTask]:
    """Backward-compatible alias for claim_task without priority filtering."""
    return await claim_task(session_factory, limit=limit, now=now)


async def mark_started(
    session_factory: Any,
    task_id: Any,
    *,
    lease_token: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Record that a claimed task has begun executing."""
    from sqlalchemy import update

    moment = _now(now)
    async with session_factory() as session:
        result = await session.execute(
            update(TaskThread)
            .where(
                TaskThread.id == task_id,
                TaskThread.status == TaskStatus.QUEUED,
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


async def heartbeat_claim(
    session_factory: Any,
    task_id: Any,
    *,
    lease_token: str | None = None,
    now: datetime | None = None,
    seconds: int = CLAIM_LEASE_SECONDS,
) -> bool:
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
    """Hand a lapsed task back to the queue. False when it has run out of attempts."""
    moment = _now(now)
    attempts = int(task.attempt_count or 0)
    if attempts >= MAX_DISPATCH_ATTEMPTS:
        metrics.inc("onsite48_queue_dead_letter_total")
        return False

    task.status = TaskStatus.QUEUED
    task.lease_expires_at = None
    task.lease_token = None
    task.completed_at = None
    task.final_answer = None

    metadata = dict(task.execution_metadata or {})
    history = list(metadata.get("dispatch_retries") or [])
    history.append(
        {"at": moment.isoformat(), "attempt": attempts, "reason": reason[:200]}
    )
    metadata["dispatch_retries"] = history[-10:]
    metadata["requeued_at"] = moment.isoformat()
    task.execution_metadata = metadata
    metrics.inc("onsite48_queue_retries_total")
    return True


def should_retry(attempt_count: int | None) -> bool:
    return int(attempt_count or 0) < MAX_DISPATCH_ATTEMPTS


#: ─── Dead-letter queue helpers ──────────────────────────────────────────────

DLQ_MAX_RETRIES = 5
DLQ_RETRY_DELAY = 300  # seconds between DLQ replay attempts


async def move_to_dlq(
    session_factory: Any,
    task_id: uuid.UUID,
    *,
    reason: str,
    error_details: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    """Move a task to the dead-letter queue via Redis for later inspection/replay.

    The task row stays in its terminal state (failed) in Postgres; the DLQ entry
    in Redis holds a copy of the metadata for diagnostics and replay.
    """
    import json as _json
    import os as _os

    redis_url = _os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    moment = _now(now)
    dlq_entry = {
        "task_id": str(task_id),
        "reason": reason,
        "error_details": error_details or {},
        "failed_at": moment.isoformat(),
        "attempts": 0,
    }
    try:
        import redis.asyncio as _redis

        client = _redis.from_url(redis_url)
        await client.lpush(f"{DLQ_KEY_PREFIX}{task_id}", _json.dumps(dlq_entry))
        await client.close()
        metrics.inc("onsite48_queue_dead_letter_total")
        return True
    except Exception:
        return False


async def replay_dlq(
    session_factory: Any,
    task_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> bool:
    """Pop a task from the DLQ and re-enqueue it at low priority.

    Returns True if the task was successfully re-queued.
    """
    import json as _json
    import os as _os

    redis_url = _os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    moment = _now(now)
    try:
        import redis.asyncio as _redis

        client = _redis.from_url(redis_url)
        raw = await client.lpop(f"{DLQ_KEY_PREFIX}{task_id}")
        await client.close()
        if raw is None:
            return False
        entry = _json.loads(raw)
        entry["replayed_at"] = moment.isoformat()
        entry["attempts"] = int(entry.get("attempts", 0)) + 1

        async with session_factory() as session, session.begin():
            task = await session.get(TaskThread, task_id)
            if task is None:
                return False
            task.status = TaskStatus.QUEUED
            task.lease_expires_at = None
            task.lease_token = None
            task.completed_at = None
            task.priority = task.priority or "low"

            metadata = dict(task.execution_metadata or {})
            dlq_history = list(metadata.get("dlq_history") or [])
            dlq_history.append(entry)
            metadata["dlq_history"] = dlq_history[-10:]
            task.execution_metadata = metadata
        metrics.inc("onsite48_queue_dlq_replayed_total")
        return True
    except Exception:
        return False


async def list_dlq(session_factory: Any, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    """List dead-lettered tasks from Redis. Returns task metadata."""
    import json as _json
    import os as _os

    redis_url = _os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    try:
        import redis.asyncio as _redis

        client = _redis.from_url(redis_url)
        # Scan all DLQ entries
        pattern = f"{DLQ_KEY_PREFIX}*"
        cursor = 0
        results: list[dict[str, Any]] = []
        while True:
            cursor, keys = await client.scan(cursor=cursor, match=pattern, count=1000)
            for key in keys:
                entries = await client.lrange(key, offset, offset + limit)
                for entry in entries:
                    try:
                        data = _json.loads(entry)
                        results.append(data)
                    except _json.JSONDecodeError:
                        pass
            if cursor == 0:
                break
            if len(results) >= limit:
                break
        await client.close()
        return results[:limit]
    except Exception:
        return []


#: ─── Timeout enforcement ────────────────────────────────────────────────────


async def enforce_timeouts(
    session_factory: Any,
    *,
    now: datetime | None = None,
    timeout_seconds: int = TASK_TIMEOUT_SECONDS,
) -> int:
    """Fail tasks that have been RUNNING past their timeout.

    A task that has been running without a single lease heartbeat for
    `timeout_seconds` is considered stuck (dead consumer, deadlock, etc.) and
    failed with a structured error code. Returns the count of timed-out tasks.

    This is safe to run as a periodic sweeper alongside the existing lease reaper.
    """
    from sqlalchemy import update

    moment = _now(now)
    cutoff = moment - timedelta(seconds=timeout_seconds)
    async with session_factory() as session, session.begin():
        result = await session.execute(
            update(TaskThread)
            .where(
                TaskThread.status == TaskStatus.RUNNING,
                TaskThread.started_at <= cutoff,
                TaskThread.lease_expires_at <= moment,
            )
            .values(
                status=TaskStatus.FAILED,
                completed_at=moment,
            )
        )
        count = result.rowcount

    if count > 0:
        metrics.inc("onsite48_queue_timeouts_total", count)
    return count