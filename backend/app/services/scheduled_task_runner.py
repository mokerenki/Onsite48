"""Execute one durable, worker-claimed scheduled agent task occurrence.

The proactive worker claims an occurrence and writes ``ScheduledTaskExecution``
inside its database transaction. This module never creates a second execution
record; it creates or resumes the task thread attached to that claim and keeps
the execution lease fresh while the canonical task stream is active.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.realtime import record_event
from app.api.websocket_handler import stream_task_events
from app.core.task_presentation import concise_task_title, ensure_task_presentation
from app.services.notifications import create_notification
from app.memory.temporal_workflows import AgentTaskInput, get_temporal_dual_write
from app.models import ScheduledAgentTask, ScheduledTaskExecution, TaskStatus, TaskThread
import structlog

logger = structlog.get_logger("onsite48.scheduled_runner")

TERMINAL_EVENT_TYPES = {"task_complete", "task_error", "needs_confirmation", "needs_user_intervention", "needs_clarification", "task_cancelled"}
TERMINAL_EXECUTION_STATUSES = {"completed", "needs_approval", "needs_user_intervention", "failed", "cancelled"}
DEFAULT_LEASE_SECONDS = 15 * 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _execution_status(event_type: str, event: dict[str, Any]) -> str:
    if event_type == "task_complete" or str(event.get("status") or "") in {"completed", "complete"}:
        return "completed"
    if event_type == "needs_confirmation":
        return "needs_approval"
    if event_type == "needs_user_intervention":
        return "needs_user_intervention"
    if event_type == "needs_clarification":
        return "needs_user_intervention"
    if event_type == "task_cancelled":
        return "cancelled"
    return "failed"


def _event_message(event: dict[str, Any]) -> str:
    return str(event.get("final_answer") or event.get("message") or event.get("reason") or event.get("error") or "")[:8_000]


async def _renew_execution_lease(
    session_factory: async_sessionmaker[AsyncSession], execution_id: Any, *, now: datetime | None = None
) -> None:
    """Record liveness without coupling scheduled work to an interactive client."""
    observed_at = now or _utcnow()
    async with session_factory() as session:
        execution = await session.get(ScheduledTaskExecution, execution_id)
        if execution is None or execution.status in TERMINAL_EXECUTION_STATUSES:
            return
        execution.heartbeat_at = observed_at
        execution.lease_expires_at = observed_at + timedelta(seconds=DEFAULT_LEASE_SECONDS)
        await session.commit()


async def run_scheduled_agent_task(
    session_factory: async_sessionmaker[AsyncSession],
    schedule_id: Any,
    *,
    execution_id: Any,
    checkpointer: Any,
    model_router: Any,
    tool_registry: Any,
    domain_router: Any,
) -> str:
    """Execute an already claimed occurrence through the canonical task runtime.

    The runner deliberately has no WebSocket dependency. A valid execution can
    complete, fail, or persist an approval/intervention pause with no frontend
    connected. Existing running work is resumed from its checkpoint when a
    worker recovers an expired lease.
    """
    thread_id = ""
    terminal_status = "failed"
    terminal_answer = ""
    resume_from_checkpoint = False

    async with session_factory() as session:
        schedule = await session.get(ScheduledAgentTask, schedule_id)
        execution = await session.get(ScheduledTaskExecution, execution_id)
        if schedule is None or execution is None or execution.schedule_id != schedule.id:
            return "failed"
        if execution.status in TERMINAL_EXECUTION_STATUSES:
            return execution.status
        if not schedule.enabled:
            execution.status = "cancelled"
            execution.completed_at = _utcnow()
            execution.lease_expires_at = None
            await session.commit()
            return "cancelled"

        now = _utcnow()
        execution.status = "running"
        execution.heartbeat_at = now
        execution.lease_expires_at = now + timedelta(seconds=DEFAULT_LEASE_SECONDS)

        if execution.task_thread_id:
            thread_id = execution.task_thread_id
            existing_task = await session.scalar(select(TaskThread).where(TaskThread.external_thread_id == thread_id))
            if existing_task is not None:
                # A recovered execution must continue the same task identity.
                # ``stream_task_events`` will return a typed task error if its
                # graph checkpoint is unavailable; the worker then schedules a
                # normal bounded retry instead of silently discarding work.
                resume_from_checkpoint = True
            else:
                execution.task_thread_id = None
                thread_id = ""

        if not thread_id:
            thread_id = f"schedule-{schedule.id}-{uuid.uuid4().hex[:12]}"
            task = TaskThread(
                external_thread_id=thread_id,
                user_id=schedule.user_id,
                organization_id=schedule.organization_id,
                title=schedule.title or concise_task_title(schedule.prompt),
                prompt=schedule.prompt,
                domain="scheduled",
                status=TaskStatus.QUEUED,
                execution_metadata=ensure_task_presentation({
                    "conversation": [{"turn_id": str(uuid.uuid4()), "role": "user", "content": schedule.prompt}],
                    "scheduled_task_id": str(schedule.id),
                    "scheduled_execution_id": str(execution.id),
                    "schedule_type": schedule.schedule_type,
                    "autonomy_policy": schedule.autonomy_policy or {"mode": "approval_required"},
                }, schedule.prompt, "scheduled"),
            )
            execution.task_thread_id = thread_id
            schedule.last_task_thread_id = thread_id
            session.add(task)
        await session.commit()

        # Phase 5: Temporal dual-write shadow submission (non-blocking)
        try:
            temporal = get_temporal_dual_write()
            if temporal.enabled and temporal.available:
                _temporal_input = AgentTaskInput(
                    thread_id=thread_id,
                    user_id=str(schedule.user_id),
                    organization_id=str(schedule.organization_id),
                    prompt=schedule.prompt,
                    domain="scheduled",
                    autonomy_policy=schedule.autonomy_policy or {"mode": "approval_required"},
                    schedule_id=str(schedule.id),
                    execution_id=str(execution.id),
                )
                _shadow_run_id = await temporal.submit_scheduled_task(_temporal_input)
                if _shadow_run_id:
                    execution.temporal_shadow_run_id = _shadow_run_id
                    await session.commit()
        except Exception as _temporal_exc:
            logger.debug("scheduled_runner.temporal_shadow_skipped", error=str(_temporal_exc)[:120])

    try:
        async for event in stream_task_events(
            schedule.prompt,
            str(schedule.user_id),
            str(schedule.organization_id),
            thread_id,
            checkpointer,
            model_router,
            tool_registry,
            domain_router,
            extra_state={"schedule_authorization": schedule.autonomy_policy or {"mode": "approval_required"}},
            resume_from_checkpoint=resume_from_checkpoint,
        ):
            async with session_factory() as session:
                task = await session.scalar(select(TaskThread).where(TaskThread.external_thread_id == thread_id))
                if task is not None:
                    await record_event(session, task, event)
                    await session.commit()
            await _renew_execution_lease(session_factory, execution_id)
            event_type = str(event.get("type") or "")
            if event_type in TERMINAL_EVENT_TYPES:
                terminal_status = _execution_status(event_type, event)
                terminal_answer = _event_message(event)
                break
        else:
            terminal_status = "failed"
            terminal_answer = "Scheduled task stream ended without a terminal task event."
    except Exception as exc:
        terminal_status = "failed"
        terminal_answer = f"Scheduled task failed before completion: {str(exc)[:800]}"
        async with session_factory() as session:
            task = await session.scalar(select(TaskThread).where(TaskThread.external_thread_id == thread_id))
            if task is not None:
                await record_event(session, task, {
                    "type": "task_error",
                    "error_code": "scheduled_runtime_failure",
                    "message": terminal_answer,
                })
                await session.commit()
    finally:
        async with session_factory() as session:
            schedule = await session.get(ScheduledAgentTask, schedule_id)
            execution = await session.get(ScheduledTaskExecution, execution_id)
            now = _utcnow()
            if execution is not None:
                execution.status = terminal_status
                execution.completed_at = now
                execution.heartbeat_at = now
                execution.lease_expires_at = None
                execution.summary = terminal_answer[:8_000] or None
                execution.error = terminal_answer[:1_000] if terminal_status == "failed" else None
            if schedule is not None:
                schedule.last_task_thread_id = thread_id or schedule.last_task_thread_id
                if terminal_status == "completed":
                    schedule.failure_count = 0
                    schedule.last_error = None
                elif terminal_status == "needs_approval":
                    schedule.last_error = "Scheduled task is waiting for your approval in the task workspace."
                elif terminal_status == "needs_user_intervention":
                    schedule.last_error = terminal_answer or "Scheduled task needs a human verification step in the task workspace."
                elif terminal_status == "cancelled":
                    schedule.last_error = "Scheduled task was cancelled."
                else:
                    schedule.failure_count = (schedule.failure_count or 0) + 1
                    schedule.last_error = terminal_answer or "Scheduled task failed."
                await create_notification(
                    session,
                    organization_id=schedule.organization_id,
                    user_id=schedule.user_id,
                    notification_type="scheduled_task",
                    title=f"Scheduled task: {schedule.title}",
                    body=(terminal_answer or "The scheduled task finished. The result is available in your task history.")[:1000],
                    href="/history",
                    metadata={"thread_id": thread_id, "schedule_id": str(schedule.id), "execution_id": str(execution_id), "status": terminal_status},
                )
            await session.commit()
    return terminal_status