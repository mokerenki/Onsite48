"""Durable proactive and scheduled-task worker.

Scheduled task claims are database transactions: one worker locks one due row,
creates its durable execution record, advances the schedule out of the due set,
and commits before model work begins. This prevents duplicate claims and keeps a
recoverable audit record when a worker process dies after claiming work.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AutomationSubscription, ScheduledAgentTask, ScheduledTaskExecution
from app.services.proactive_operations import build_morning_briefing
from app.services.scheduled_task_runner import DEFAULT_LEASE_SECONDS, run_scheduled_agent_task
from app.services.recurrence import next_occurrence
from app.services.task_lease import sweep_orphaned_tasks

logger = structlog.get_logger("onsite48.proactive_worker")
SCHEDULED_CLAIM_BATCH_LIMIT = 100


async def create_proactive_task_to_queue(
    task_description: str,
    context: str = "",
    user_id: str = "heartbeat",
    tenant_id: str = "default",
    priority: str = "medium",
    action_type: str = "proactive_monitoring",
) -> dict[str, Any]:
    """Dispatch a legacy heartbeat observation into the active worker stream.

    This compatibility queue is intentionally kept in the worker service, not
    in the retired HTTP task API, so heartbeat signals cannot create a second
    task execution path.
    """
    from app.core.context import onsite48

    task_id = str(uuid.uuid4())
    thread_id = str(uuid.uuid4())
    payload = {
        "task_id": task_id,
        "thread_id": thread_id,
        "task_description": task_description,
        "context": context,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "priority": priority,
        "action_type": action_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await onsite48.redis_client.xadd(
        "proactive_tasks_stream",
        {"payload": json.dumps(payload).encode("utf-8")},
    )
    return {"status": "dispatched_to_queue", "task_id": task_id}


class ProactiveOperationsWorker:
    """Run durable proactive schedules with bounded retries and recovery."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        poll_seconds: int = 60,
        max_failures: int = 5,
        retry_base_seconds: int = 60,
        retry_max_seconds: int = 3600,
        redis_client: Any | None = None,
        heartbeat_key: str = "onsite48:proactive_worker:heartbeat",
        checkpointer: Any | None = None,
        model_router: Any | None = None,
        tool_registry: Any | None = None,
        domain_router: Any | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.poll_seconds = poll_seconds
        self.max_failures = max_failures
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.redis_client = redis_client
        self.heartbeat_key = heartbeat_key
        self.checkpointer = checkpointer
        self.model_router = model_router
        self.tool_registry = tool_registry
        self.domain_router = domain_router
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._last_cycle_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._last_error: str | None = None
        self._processed_count = 0
        self._failed_count = 0
        self._dead_letter_count = 0

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running and self._task is not None and not self._task.done(),
            "poll_seconds": self.poll_seconds,
            "last_cycle_at": self._iso(self._last_cycle_at),
            "last_success_at": self._iso(self._last_success_at),
            "last_error": self._last_error,
            "processed_count": self._processed_count,
            "failed_count": self._failed_count,
            "dead_letter_count": self._dead_letter_count,
        }

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    @staticmethod
    def _error_text(exc: Exception) -> str:
        return str(exc).replace("\n", " ")[:1000] or type(exc).__name__

    def _runtime_missing_dependencies(self) -> list[str]:
        dependencies = {
            "checkpointer": self.checkpointer,
            "model_router": self.model_router,
            "tool_registry": self.tool_registry,
            "domain_router": self.domain_router,
        }
        return [name for name, value in dependencies.items() if value is None]

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="onsite48-proactive-worker")
        logger.info("proactive_worker.started", poll_seconds=self.poll_seconds)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None
        if self.redis_client is not None:
            try:
                await self.redis_client.delete(self.heartbeat_key)
            except Exception:
                logger.warning("proactive_worker.heartbeat_delete_failed")
        logger.info("proactive_worker.stopped")

    async def _run(self) -> None:
        while self._running:
            self._last_cycle_at = datetime.now(timezone.utc)
            await self._write_heartbeat()
            try:
                await self._run_due_briefings()
                await self._run_due_scheduled_agent_tasks()
                await self._sweep_orphaned_task_leases()
                if not self._runtime_missing_dependencies():
                    self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = self._error_text(exc)
                logger.exception("proactive_worker.iteration_failed", error=self._last_error)
            await self._write_heartbeat()
            await asyncio.sleep(self.poll_seconds)

    async def _sweep_orphaned_task_leases(self) -> None:
        """Fail accepted task work that no process is running any more.

        REST acceptance dispatches through in-process BackgroundTasks, so a
        restart strands the task at ``queued`` with no terminal state and
        nothing to alert on. A lapsed lease on a queued task is unambiguous, so
        it is failed with a reason the user can read. Re-dispatch is
        deliberately not attempted: it would only be correct while one process
        owns the work, and would silently no-op across replicas.
        """
        try:
            failed = await sweep_orphaned_tasks(self.session_factory)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_error = self._error_text(exc)
            logger.warning("proactive_worker.orphan_sweep_failed", error=self._last_error)
            return
        if failed:
            logger.error(
                "proactive_worker.orphaned_tasks_failed",
                count=len(failed),
                thread_ids=[item["thread_id"] for item in failed],
            )

    async def _write_heartbeat(self) -> None:
        if self.redis_client is None:
            return
        try:
            await self.redis_client.set(
                self.heartbeat_key,
                datetime.now(timezone.utc).isoformat(),
                ex=max(self.poll_seconds * 3, 120),
            )
        except Exception as exc:
            logger.warning("proactive_worker.heartbeat_write_failed", error=self._error_text(exc))

    async def _run_due_briefings(self) -> None:
        async with self.session_factory() as session:
            subscription_ids = list((await session.scalars(
                select(AutomationSubscription.id).where(
                    AutomationSubscription.kind == "marketing_morning_briefing",
                    AutomationSubscription.enabled.is_(True),
                    AutomationSubscription.dead_lettered_at.is_(None),
                )
            )).all())
        for subscription_id in subscription_ids:
            await self._process_subscription(subscription_id)

    async def _run_due_scheduled_agent_tasks(self) -> None:
        """Recover expired claims, then atomically claim every currently due run."""
        missing = self._runtime_missing_dependencies()
        if missing:
            self._last_error = f"Scheduled task runtime unavailable: {', '.join(missing)}."
            logger.error(
                "scheduled_worker.runtime_unavailable",
                dependencies=missing,
                worker="proactive-worker",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            return

        for schedule_id, execution_id, scheduled_for in await self._recover_stale_scheduled_executions():
            await self._execute_claimed_scheduled_task(schedule_id, execution_id, scheduled_for, recovered=True)

        for _ in range(SCHEDULED_CLAIM_BATCH_LIMIT):
            claim = await self._claim_one_due_scheduled_task()
            if claim is None:
                break
            schedule_id, execution_id, scheduled_for = claim
            await self._execute_claimed_scheduled_task(schedule_id, execution_id, scheduled_for)

    async def _claim_one_due_scheduled_task(self) -> tuple[Any, Any, datetime] | None:
        """Claim one due occurrence and write its execution row atomically."""
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            async with session.begin():
                schedule = await session.scalar(
                    select(ScheduledAgentTask)
                    .where(
                        ScheduledAgentTask.enabled.is_(True),
                        ScheduledAgentTask.next_run_at.is_not(None),
                        ScheduledAgentTask.next_run_at <= now,
                    )
                    .order_by(ScheduledAgentTask.next_run_at.asc())
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                if schedule is None:
                    return None
                scheduled_for = schedule.next_run_at
                if scheduled_for is None:
                    return None
                execution = ScheduledTaskExecution(
                    schedule_id=schedule.id,
                    organization_id=schedule.organization_id,
                    user_id=schedule.user_id,
                    attempt=max(1, int(schedule.failure_count or 0) + 1),
                    status="claimed",
                    scheduled_for=scheduled_for,
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=DEFAULT_LEASE_SECONDS),
                )
                session.add(execution)
                # Removing this occurrence from the due set happens in the same
                # transaction as execution creation. A crash cannot lose it.
                schedule.next_run_at = None
                await session.flush()
                return schedule.id, execution.id, scheduled_for

    async def _recover_stale_scheduled_executions(self) -> list[tuple[Any, Any, datetime]]:
        """Lease exactly one stale execution to this worker for checkpoint resume."""
        now = datetime.now(timezone.utc)
        recovered: list[tuple[Any, Any, datetime]] = []
        async with self.session_factory() as session:
            async with session.begin():
                executions = list((await session.scalars(
                    select(ScheduledTaskExecution)
                    .where(
                        ScheduledTaskExecution.status.in_(("claimed", "running", "recoverable")),
                        ScheduledTaskExecution.lease_expires_at.is_not(None),
                        ScheduledTaskExecution.lease_expires_at <= now,
                    )
                    .order_by(ScheduledTaskExecution.lease_expires_at.asc())
                    .with_for_update(skip_locked=True)
                    .limit(SCHEDULED_CLAIM_BATCH_LIMIT)
                )).all())
                for execution in executions:
                    schedule = await session.get(ScheduledAgentTask, execution.schedule_id, with_for_update=True)
                    if schedule is None or not schedule.enabled:
                        execution.status = "cancelled"
                        execution.completed_at = now
                        execution.lease_expires_at = None
                        continue
                    execution.status = "recoverable"
                    execution.heartbeat_at = now
                    execution.lease_expires_at = now + timedelta(seconds=DEFAULT_LEASE_SECONDS)
                    recovered.append((schedule.id, execution.id, execution.scheduled_for))
        if recovered:
            logger.warning("scheduled_worker.recovering_stale_executions", count=len(recovered))
        return recovered

    @staticmethod
    def _next_recurring_occurrence(schedule: ScheduledAgentTask, scheduled_for: datetime, now: datetime) -> datetime:
        """Advance from the claimed occurrence using the stored local policy."""
        policy = dict(getattr(schedule, "schedule_policy", {}) or {})
        policy.setdefault("frequency", getattr(schedule, "schedule_type", "daily"))
        policy.setdefault("timezone", getattr(schedule, "timezone", "UTC"))
        policy.setdefault("run_at", scheduled_for)
        return next_occurrence(policy, max(now, scheduled_for))

    @staticmethod
    def _next_daily_occurrence(schedule: ScheduledAgentTask, scheduled_for: datetime, now: datetime) -> datetime:
        """Backward-compatible helper retained for existing worker tests."""
        if not hasattr(schedule, "schedule_policy"):
            try:
                zone = ZoneInfo(schedule.timezone)
            except Exception:
                zone = timezone.utc
            candidate = scheduled_for.astimezone(zone) + timedelta(days=1)
            while candidate.astimezone(timezone.utc) <= now:
                candidate += timedelta(days=1)
            return candidate.astimezone(timezone.utc)
        return ProactiveOperationsWorker._next_recurring_occurrence(schedule, scheduled_for, now)

    async def _execute_claimed_scheduled_task(
        self, schedule_id: Any, execution_id: Any, scheduled_for: datetime, *, recovered: bool = False
    ) -> None:
        outcome = await run_scheduled_agent_task(
            self.session_factory,
            schedule_id,
            execution_id=execution_id,
            checkpointer=self.checkpointer,
            model_router=self.model_router,
            tool_registry=self.tool_registry,
            domain_router=self.domain_router,
        )
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            async with session.begin():
                schedule = await session.get(ScheduledAgentTask, schedule_id, with_for_update=True)
                execution = await session.get(ScheduledTaskExecution, execution_id, with_for_update=True)
                if schedule is None or execution is None:
                    return
                schedule.last_run_at = now
                if outcome == "failed":
                    self._failed_count += 1
                    failures = int(schedule.failure_count or 0)
                    if failures >= self.max_failures:
                        schedule.enabled = False
                        schedule.next_run_at = None
                        schedule.last_error = (schedule.last_error or "Scheduled task failed.")[:850] + " Retry limit reached; reopen and retry it manually from Automations."
                        self._dead_letter_count += 1
                        logger.error("proactive_worker.scheduled_task_dead_lettered", schedule_id=str(schedule_id), failures=failures)
                    else:
                        delay_seconds = min(self.retry_max_seconds, self.retry_base_seconds * (2 ** max(0, failures - 1)))
                        schedule.next_run_at = now + timedelta(seconds=delay_seconds)
                        logger.warning("proactive_worker.scheduled_task_retry_scheduled", schedule_id=str(schedule_id), failures=failures, retry_in_seconds=delay_seconds)
                elif outcome in {"needs_approval", "needs_user_intervention"}:
                    # Do not create duplicate human-gated work. Daily schedules
                    # may proceed to their next future occurrence; one-time work
                    # remains visible in its durable paused task record.
                    if schedule.schedule_type in {"daily", "weekly", "monthly"} and schedule.enabled:
                        schedule.next_run_at = self._next_recurring_occurrence(schedule, scheduled_for, now)
                    else:
                        schedule.enabled = False
                        schedule.next_run_at = None
                    self._processed_count += 1
                elif outcome == "completed" and schedule.schedule_type in {"daily", "weekly", "monthly"} and schedule.enabled:
                    schedule.next_run_at = self._next_recurring_occurrence(schedule, scheduled_for, now)
                    self._processed_count += 1
                    self._last_success_at = now
                elif outcome == "completed":
                    schedule.enabled = False
                    schedule.next_run_at = None
                    self._processed_count += 1
                    self._last_success_at = now
                else:
                    # Cancellation or a typed terminal outcome that should not
                    # automatically rerun remains visible and non-runnable.
                    schedule.enabled = False
                    schedule.next_run_at = None
                    self._processed_count += 1
                if recovered:
                    logger.info("scheduled_worker.stale_execution_recovered", schedule_id=str(schedule_id), execution_id=str(execution_id), outcome=outcome)

    async def _process_subscription(self, subscription_id: Any) -> None:
        try:
            async with self.session_factory() as session:
                subscription = await session.get(AutomationSubscription, subscription_id)
                if subscription is None or not subscription.enabled or subscription.dead_lettered_at:
                    return
                now = datetime.now(timezone.utc)
                if not self._is_due(subscription, now):
                    return

                await build_morning_briefing(
                    session,
                    organization_id=subscription.organization_id,
                    user_id=subscription.user_id,
                )
                subscription.last_run_at = now
                subscription.next_run_at = None
                subscription.failure_count = 0
                subscription.last_error = None
                subscription.last_error_at = None
                await session.commit()

            self._processed_count += 1
            self._last_success_at = datetime.now(timezone.utc)
            logger.info("proactive_worker.briefing_generated", subscription_id=str(subscription_id))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._failed_count += 1
            error = self._error_text(exc)
            self._last_error = error
            await self._record_failure(subscription_id, error)

    def _is_due(self, subscription: AutomationSubscription, now: datetime) -> bool:
        if subscription.next_run_at and subscription.next_run_at > now:
            return False
        try:
            local_now = now.astimezone(ZoneInfo(subscription.timezone))
        except Exception:
            local_now = now
        schedule = subscription.schedule or {}
        try:
            target_hour = min(23, max(0, int(schedule.get("hour", 8))))
            target_minute = min(59, max(0, int(schedule.get("minute", 0))))
        except (TypeError, ValueError):
            target_hour, target_minute = 8, 0
        already_run_today = subscription.last_run_at is not None and subscription.last_run_at.astimezone(local_now.tzinfo).date() == local_now.date()
        return not already_run_today and (local_now.hour, local_now.minute) >= (target_hour, target_minute)

    async def _record_failure(self, subscription_id: Any, error: str) -> None:
        try:
            async with self.session_factory() as session:
                subscription = await session.get(AutomationSubscription, subscription_id)
                if subscription is None:
                    return
                now = datetime.now(timezone.utc)
                subscription.failure_count = (subscription.failure_count or 0) + 1
                subscription.last_error = error
                subscription.last_error_at = now
                if subscription.failure_count >= self.max_failures:
                    subscription.dead_lettered_at = now
                    subscription.next_run_at = None
                    self._dead_letter_count += 1
                    logger.error("proactive_worker.subscription_dead_lettered", subscription_id=str(subscription_id), failures=subscription.failure_count, error=error)
                else:
                    delay_seconds = min(self.retry_max_seconds, self.retry_base_seconds * (2 ** (subscription.failure_count - 1)))
                    subscription.next_run_at = now + timedelta(seconds=delay_seconds)
                    logger.warning("proactive_worker.subscription_retry_scheduled", subscription_id=str(subscription_id), failures=subscription.failure_count, retry_in_seconds=delay_seconds, error=error)
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as persistence_exc:
            logger.exception("proactive_worker.failure_state_persist_failed", subscription_id=str(subscription_id), error=self._error_text(persistence_exc))