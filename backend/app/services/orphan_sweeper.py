"""Periodic sweeper for orphaned / lapsed task claims.

Two independent recovery paths share this module:

* ``enforce_timeouts`` (from task_queue) fails RUNNING tasks whose lease and
  started_at are both past the timeout horizon.
* ``requeue_lapsed_queued_claims`` hands QUEUED rows with an expired lease
  back to the queue via ``release_for_retry`` so another worker can pick them up.

Both halves are wrapped so a failure in one never kills the cycle. The
task-worker process also writes a dedicated Redis heartbeat key so its
docker-compose healthcheck can distinguish it from the proactive worker.
"""
from __future__ import annotations

import asyncio
import os
import random
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select

from app.core.platform_metrics import metrics
from app.models import TaskStatus, TaskThread
from app.services.task_queue import enforce_timeouts, release_for_retry

logger = structlog.get_logger("onsite48.orphan_sweeper")

SWEEP_INTERVAL_SECONDS = float(os.getenv("TASK_SWEEP_INTERVAL_SECONDS", "60"))
TASK_WORKER_HEARTBEAT_TTL_SECONDS = int(os.getenv("TASK_WORKER_HEARTBEAT_TTL_SECONDS", "90"))
TASK_WORKER_HEARTBEAT_KEY = "onsite48:task_worker:heartbeat"


def _now() -> datetime:
    return datetime.now(UTC)


async def requeue_lapsed_queued_claims(
    session_factory: Any,
    *,
    limit: int = 50,
    now: datetime | None = None,
) -> int:
    """Requeue QUEUED tasks whose claim lease has lapsed.

    Selects rows with ``status == QUEUED``, a non-null ``lease_expires_at`` that
    is at or before *now*, ordered by ``updated_at``, with ``SKIP LOCKED`` so
    concurrent sweepers do not contend. Each row is handed to
    ``release_for_retry``; rows that have exhausted attempts are left alone
    (the helper returns False).
    """
    moment = now or _now()
    requeued = 0

    async with session_factory() as session, session.begin():
        query = (
            select(TaskThread)
            .where(
                TaskThread.status == TaskStatus.QUEUED,
                TaskThread.lease_expires_at.is_not(None),
                TaskThread.lease_expires_at <= moment,
            )
            .order_by(TaskThread.updated_at.asc())
            .with_for_update(skip_locked=True)
            .limit(max(1, limit))
        )
        candidates = (await session.scalars(query)).all()

        for task in candidates:
            ok = await release_for_retry(
                session,
                task,
                now=moment,
                reason="lapsed claim reaped by orphan sweeper",
            )
            if ok:
                requeued += 1

    if requeued:
        metrics.inc("onsite48_orphan_sweeper_requeued_total", requeued)
    return requeued


async def sweep_once(session_factory: Any) -> dict[str, int]:
    """Run one sweep cycle. Each half is isolated so one failure never kills the other."""
    timed_out = 0
    requeued = 0

    try:
        timed_out = await enforce_timeouts(session_factory)
    except Exception as exc:
        logger.error(
            "orphan_sweeper.enforce_timeouts_failed",
            error_type=type(exc).__name__,
            error=str(exc)[:400],
        )

    try:
        requeued = await requeue_lapsed_queued_claims(session_factory)
    except Exception as exc:
        logger.error(
            "orphan_sweeper.requeue_failed",
            error_type=type(exc).__name__,
            error=str(exc)[:400],
        )

    metrics.set("onsite48_orphan_sweeper_last_timed_out", timed_out)
    metrics.set("onsite48_orphan_sweeper_last_requeued", requeued)
    logger.info(
        "orphan_sweeper.cycle",
        timed_out=timed_out,
        requeued=requeued,
    )
    return {"timed_out": timed_out, "requeued": requeued}


async def run_orphan_sweeper(
    session_factory: Any,
    stop: asyncio.Event | None = None,
) -> None:
    """Loop ``sweep_once`` with jittered sleep until *stop* is set."""
    stop_event = stop or asyncio.Event()
    logger.info("orphan_sweeper.started", interval_s=SWEEP_INTERVAL_SECONDS)
    while not stop_event.is_set():
        try:
            await sweep_once(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A fully broken cycle must not kill the process.
            logger.error(
                "orphan_sweeper.cycle_failed",
                error_type=type(exc).__name__,
                error=str(exc)[:400],
            )

        jitter = random.uniform(0.8, 1.2)
        timeout = SWEEP_INTERVAL_SECONDS * jitter
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            continue

    logger.info("orphan_sweeper.stopped")


def start_orphan_sweeper(
    session_factory: Any,
    stop: asyncio.Event | None = None,
) -> asyncio.Task:
    """Spawn the sweeper as a managed asyncio.Task and return it."""
    return asyncio.create_task(
        run_orphan_sweeper(session_factory, stop=stop),
        name="orphan_sweeper",
    )


async def write_task_worker_heartbeat(redis_client: Any, worker_id: str) -> None:
    """SET the task-worker heartbeat key. Swallows all exceptions."""
    try:
        value = f"{worker_id}:{_now().isoformat()}"
        await redis_client.set(
            TASK_WORKER_HEARTBEAT_KEY,
            value,
            ex=TASK_WORKER_HEARTBEAT_TTL_SECONDS,
        )
    except Exception as exc:
        logger.warning(
            "orphan_sweeper.heartbeat_failed",
            worker_id=worker_id,
            error=str(exc)[:200],
        )


__all__ = [
    "SWEEP_INTERVAL_SECONDS",
    "requeue_lapsed_queued_claims",
    "sweep_once",
    "run_orphan_sweeper",
    "start_orphan_sweeper",
    "write_task_worker_heartbeat",
    "TASK_WORKER_HEARTBEAT_KEY",
]
