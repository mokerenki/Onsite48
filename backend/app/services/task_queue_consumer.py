"""The consumer loop: claim queued tasks, run them, repeat.

This is the process that makes accepted REST work survive the process that
accepted it. `BackgroundTasks` ran the task in the accepting process after the
response was sent, so a restart between accept and run lost the work and left a
row that could only be failed. Here the database holds the work, and this loop
takes it.

Two properties matter more than throughput:

* **A claim is held for a bounded time.** The lease is refreshed while the task
  is producing events, so a slow tool call does not lose its claim, and a
  consumer that dies has its work taken back after the lease rather than
  blocking the queue indefinitely.
* **A crash is a retry, not a failure.** The sweeper requeues a lapsed task
  while it has attempts left. This loop is where that second attempt is picked
  up, so the retry is not just recorded but actually run.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import structlog

from app.services.task_queue import ClaimedTask, claim_next

logger = structlog.get_logger("onsite48.task_queue_consumer")

#: Default seconds between polls when the queue is empty.
#:
#: Short enough that a task accepted a moment ago starts promptly rather than
#: after a visible lag; long enough that an idle consumer is not a busy loop
#: against the database. The consumer also wakes immediately on a task it
#: claimed, so this bounds the *idle* wait only.
DEFAULT_POLL_SECONDS = 1.0


async def run_once(
    session_factory: Any,
    execute: Callable[[ClaimedTask], Awaitable[None]],
    *,
    batch: int = 5,
    now: Any = None,
) -> int:
    """Claim and run one batch. Returns how many tasks were dispatched.

    Exposed separately from the loop so tests and a one-shot cron can drive the
    queue without starting a background task.
    """
    claimed = await claim_next(session_factory, limit=batch, now=now)
    if not claimed:
        return 0

    for task in claimed:
        logger.info(
            "task_queue.dispatched",
            task_id=str(task.id),
            thread_id=task.external_thread_id,
            attempt=task.attempt,
        )
        try:
            await execute(task)
        except Exception as exc:
            # One task's failure must not stop the batch. The claim is left to
            # lapse, and the sweeper requeues it if it has attempts left -- so
            # the work is recovered without this loop knowing or caring why it
            # failed, which keeps a poison task from stopping the queue.
            logger.error(
                "task_queue.dispatch_failed",
                task_id=str(task.id),
                thread_id=task.external_thread_id,
                attempt=task.attempt,
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )
    return len(claimed)


async def consume_forever(
    session_factory: Any,
    execute: Callable[[ClaimedTask], Awaitable[None]],
    *,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    batch: int = 5,
    stop: asyncio.Event | None = None,
) -> None:
    """Poll until stopped. Never raises; a bad poll must not kill the consumer."""
    stop_event = stop or asyncio.Event()
    logger.info("task_queue.consumer_started", poll_seconds=poll_seconds, batch=batch)
    while not stop_event.is_set():
        try:
            dispatched = await run_once(session_factory, execute, batch=batch)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A database blip must not end the process: a dead consumer means no
            # queued task starts at all, which is a far worse failure than a
            # failed poll.
            logger.error(
                "task_queue.poll_failed",
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )
            dispatched = 0

        if dispatched:
            # More work is probably waiting; go straight back rather than
            # sleeping through a backlog.
            continue
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
        except asyncio.TimeoutError:
            continue

    logger.info("task_queue.consumer_stopped")