"""Dedicated database-backed task worker.

Run with ``python -m app.services.task_worker_entrypoint``. It shares the
application lifespan for model/tool initialization but does not bind an HTTP
port. Multiple replicas safely share the Postgres queue through SKIP LOCKED.
"""
from __future__ import annotations

import asyncio
import os
import socket
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

from app.api.product_tasks import _run_rest_task
from app.core.context import onsite48 as runtime
from app.core.platform_metrics import metrics
from app.db import get_session_factory
from app.main import app, lifespan
from app.models import TaskStatus, TaskThread
from app.services.task_queue import ClaimedTask, heartbeat_claim, is_cancelled, mark_started
from app.services.task_queue_consumer import consume_forever

logger = structlog.get_logger("onsite48.task_worker")

WORKER_ID = os.getenv("WORKER_ID", f"task-worker-{socket.gethostname()}-{uuid.uuid4().hex[:8]}")


def _terminal_task_event(status: str, summary: str) -> dict:
    """Map a loop-runtime result status to a durable task-level event.

    ``run_finished``/``run_failed`` are journal-level events: neither maps
    to a lifecycle phase nor a TaskStatus, so ``record_event`` persisted
    them without moving task_threads.status off RUNNING, leaving zombie
    tasks that the orphan sweeper later reaped to FAILED. The events
    below are the ones the state machine actually applies.
    """
    if status == "failed":
        return {
            "type": "task_error",
            "status": "failed",
            "retryable": False,
            "error_code": "run_failed",
            "message": summary,
            "final_answer": summary,
        }
    if status == "cancelled":
        return {"type": "task_complete", "status": "cancelled", "final_answer": summary}
    if status == "partial":
        return {"type": "task_complete", "status": "partial", "final_answer": summary}
    if status == "waiting_approval":
        return {"type": "needs_confirmation"}
    if status in ("needs_input", "needs_user"):
        return {"type": "needs_user_intervention"}
    return {"type": "task_complete", "status": "completed", "final_answer": summary}


async def _fail_claimed_task(session_factory: Any, claim: ClaimedTask, exc: BaseException) -> None:
    """Record a terminal task_error for a claim that crashed mid-execution.

    Belt-and-braces: also force status=FAILED + completed_at so the row can
    never stay RUNNING forever even if record_event is somehow a no-op.
    This helper itself must never raise — cleanup must not mask the original
    error or kill the worker.
    """
    try:
        from app.api.realtime import record_event

        now = datetime.now(timezone.utc)
        async with session_factory() as session:
            task = await session.get(TaskThread, claim.id)
            if task is None:
                return
            # Already terminal — do not repaint.
            if task.status in {
                TaskStatus.COMPLETED,
                TaskStatus.PARTIAL,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }:
                return
            event = {
                "type": "task_error",
                "status": "failed",
                "retryable": True,
                "error_code": "worker_execution_failed",
                "message": (
                    "The task stopped because of an internal execution error. "
                    "You can run it again."
                ),
                "thread_id": claim.external_thread_id,
                "error_type": type(exc).__name__,
            }
            await record_event(session, task, event)
            # Belt-and-braces in case the state machine ignored the event.
            task.status = TaskStatus.FAILED
            task.completed_at = now
            await session.commit()
    except Exception as cleanup_exc:
        logger.error(
            "task_worker.fail_claimed_task_cleanup_failed",
            task_id=str(claim.id),
            error_type=type(cleanup_exc).__name__,
            error=str(cleanup_exc)[:400],
        )


async def execute_claim(claim: ClaimedTask) -> None:
    session_factory = get_session_factory()
    if await is_cancelled(session_factory, claim.id):
        metrics.inc("onsite48_task_cancelled_total", labels={"worker": WORKER_ID})
        return
    if not await mark_started(session_factory, claim.id, lease_token=claim.lease_token):
        metrics.inc("onsite48_task_claim_lost_total", labels={"worker": WORKER_ID})
        return
    metrics.inc("onsite48_task_started_total", labels={"worker": WORKER_ID})
    stop = asyncio.Event()

    async def renew() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=float(os.getenv("WORKER_HEARTBEAT_SECONDS", "30")),
                )
            except asyncio.TimeoutError:
                if not await heartbeat_claim(
                    session_factory, claim.id, lease_token=claim.lease_token
                ):
                    stop.set()
                    return
                metrics.inc("onsite48_worker_heartbeats_total", labels={"worker": WORKER_ID})

    heartbeat = asyncio.create_task(renew())
    started = datetime.now(timezone.utc)
    try:
        try:
            agent_runtime = os.getenv("AGENT_RUNTIME", "loop").strip().lower()
            if agent_runtime == "loop":
                from app.runtime.loop import run as loop_run
                from app.runtime.journal import journal as runtime_journal

                runtime_journal.set_session_factory(session_factory)
                ctx = {
                    "user_id": str(claim.user_id),
                    "task_id": str(claim.id),
                    "org_id": str(claim.organization_id),
                    "thread_id": claim.external_thread_id,
                }
                # research_depth is optional; populated when ClaimedTask carries it.
                depth = getattr(claim, "research_depth", None)
                if depth:
                    ctx["research_depth"] = depth
                result = await loop_run(
                    str(claim.id),
                    claim.prompt,
                    ctx,
                    session_factory=session_factory,
                    registry=getattr(runtime, "tool_registry", None),
                )
                from app.api.realtime import record_event

                async with session_factory() as session:
                    task = await session.get(TaskThread, claim.id)
                    if task is not None:
                        status = result.get("status") or "completed"
                        summary = (result.get("result") or {}).get("summary") or status
                        terminal_event = _terminal_task_event(status, summary)
                        terminal_event["thread_id"] = claim.external_thread_id
                        terminal_event["payload"] = result.get("result") or {}
                        await record_event(session, task, terminal_event)
                        await session.commit()
            else:
                await _run_rest_task(
                    task_id=claim.id,
                    thread_id=claim.external_thread_id,
                    execution_prompt=claim.prompt,
                    display_message=claim.prompt,
                    user_id=claim.user_id,
                    organization_id=claim.organization_id,
                )
            metrics.inc("onsite48_task_completed_total", labels={"worker": WORKER_ID})
        except Exception as exc:
            logger.error(
                "task_worker.claim_execution_failed",
                task_id=str(claim.id),
                thread_id=claim.external_thread_id,
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )
            metrics.inc("onsite48_task_failed_total", labels={"worker": WORKER_ID})
            await _fail_claimed_task(session_factory, claim, exc)
            raise
    finally:
        stop.set()
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        metrics.observe(
            "onsite48_task_duration_seconds",
            (datetime.now(timezone.utc) - started).total_seconds(),
            labels={"worker": WORKER_ID},
        )


async def run() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in ("SIGTERM", "SIGINT"):
        try:
            import signal

            loop.add_signal_handler(getattr(signal, signal_name), stop.set)
        except (NotImplementedError, AttributeError):
            pass

    async with lifespan(app):
        metrics.set("onsite48_worker_running", 1, labels={"worker": WORKER_ID})

        # Phase 0.2: start the orphan sweeper inside the worker process.
        sweeper_stop = asyncio.Event()
        from app.services.orphan_sweeper import start_orphan_sweeper

        sweeper = start_orphan_sweeper(get_session_factory(), stop=sweeper_stop)
        logger.info("orphan_sweeper.started", process="task-worker", worker_id=WORKER_ID)

        try:
            await consume_forever(
                get_session_factory(),
                execute_claim,
                batch=int(os.getenv("WORKER_BATCH", "3")),
                poll_seconds=float(os.getenv("WORKER_POLL_SECONDS", "1")),
                stop=stop,
                worker_id=WORKER_ID,
            )
        finally:
            sweeper_stop.set()
            await asyncio.gather(sweeper, return_exceptions=True)
            metrics.set("onsite48_worker_running", 0, labels={"worker": WORKER_ID})


if __name__ == "__main__":
    asyncio.run(run())
