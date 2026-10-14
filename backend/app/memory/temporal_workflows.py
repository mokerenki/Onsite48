"""Temporal.io workflow definitions and dual-write shadow mode.

This module defines Temporal workflows/activities that mirror the existing
LangGraph-based agent runtime. When a Temporal server is reachable, scheduled
tasks are submitted to Temporal as a shadow copy (dual-write) so the existing
Redis/LangGraph runtime remains the primary execution path. This allows a
gradual migration without downtime.

If Temporal is not configured (TEMPORAL_HOST unset) or the import fails,
the shadow mode is a no-op and all execution stays on the existing runtime.
"""
from __future__ import annotations

import os
import structlog
from dataclasses import dataclass, field
from typing import Any, Optional
from datetime import datetime, timezone

logger = structlog.get_logger("onsite48.temporal.workflows")

try:
    from temporalio import workflow
    from temporalio import activity
    TEMPORAL_AVAILABLE = True
except ImportError:
    TEMPORAL_AVAILABLE = False
    class _Stub:
        def __call__(self, *a, **kw):
            def _decorator(func):
                return func
            return _decorator
        def __getattr__(self, name):
            return self
    workflow = _Stub()  # type: ignore
    activity = _Stub()  # type: ignore


@dataclass
class AgentTaskInput:
    """Input for the agent workflow."""
    thread_id: str
    user_id: str
    organization_id: str
    prompt: str
    domain: str = "general"
    project_id: Optional[str] = None
    attachments: list[str] = field(default_factory=list)
    autonomy_policy: dict[str, Any] = field(default_factory=dict)
    schedule_id: Optional[str] = None
    execution_id: Optional[str] = None


@dataclass
class AgentActivityResult:
    """Result from the agent activity."""
    status: str = "unknown"
    final_answer: str = ""
    thread_id: str = ""
    cost_usd: float = 0.0
    error: Optional[str] = None


if TEMPORAL_AVAILABLE:
    @activity.defn
    async def execute_agent_task(input_data: AgentTaskInput) -> AgentActivityResult:
        """Activity that runs one agent task through the canonical runtime."""
        from app.runtime.loop import run
        from app.core.context import onsite48
        from app.memory.cognee_setup import CogneeMemory

        logger.info("temporal.activity.execute_agent_task.start", thread_id=input_data.thread_id)

        goal = input_data.prompt
        role_config = {
            "thread_id": input_data.thread_id,
            "user_id": input_data.user_id,
            "org_id": input_data.organization_id,
            "autonomy_policy": input_data.autonomy_policy,
        }

        result = await run(
            run_id=input_data.thread_id,
            goal=goal,
            role_config=role_config,
            user_id=input_data.user_id,
            tenant_id=input_data.organization_id,
            thread_id=input_data.thread_id,
            schedule_authorization=input_data.autonomy_policy,
            model_router=onsite48.model_router,
            tool_registry=onsite48.tool_registry,
            domain_router=onsite48.domain_router,
            checkpointer=onsite48.checkpointer,
            session_factory=None,
            event_emitter=None,
            max_steps=200,
        )

        status = result.get("status", "unknown")
        return AgentActivityResult(
            status=status,
            final_answer=result.get("summary", result.get("final_answer", "")),
            thread_id=input_data.thread_id,
            cost_usd=result.get("cost_usd", 0.0),
            error=result.get("error"),
        )

    @workflow.defn(name="agent-task-workflow")
    class AgentTaskWorkflow:
        """Temporal workflow that orchestrates a single agent task with retries."""

        @workflow.run
        async def run(self, input_data: AgentTaskInput) -> AgentActivityResult:
            logger.info("temporal.workflow.start", thread_id=input_data.thread_id)

            result = await workflow.execute_activity(
                execute_agent_task,
                input_data,
                schedule=workflow.ActivitySchedule(
                    initial_interval="0s",
                    maximum_attempts=3,
                    backtracking_second=10,
                    backoff_coefficient=2.0,
                    maximum_interval="300s",
                ),
                timeout=3600,
            )

            if result.status not in ("completed", "failed", "partial", "cancelled"):
                # Signal for manual review
                workflow.set_workflow_run_flag("pending_review")

            logger.info(
                "temporal.workflow.complete",
                thread_id=input_data.thread_id,
                status=result.status,
            )
            return result

        @workflow.query(name="result")
        def query_result(self) -> dict[str, Any]:
            """Query the current workflow result."""
            return getattr(self, "_result", {"status": "running"}) or {"status": "running"}

else:
    # Stubs for environments without temporalio installed
    async def execute_agent_task(input_data: AgentTaskInput) -> AgentActivityResult:
        return AgentActivityResult(status="not_configured", thread_id=input_data.thread_id)

    class AgentTaskWorkflow:
        pass


class TemporalDualWrite:
    """Dual-write shadow mode: submits scheduled tasks to Temporal alongside Redis runtime.

    This is a non-blocking shadow path. The Redis-based proactive_worker remains
    the authoritative execution engine. Temporal submission failures are logged
    and never propagate to the primary path.
    """

    def __init__(self):
        self._client: Optional[Any] = None
        self._task_queue = os.getenv("TEMPORAL_TASK_QUEUE", "onsite48-agent")
        self._workflow_name = "agent-task-workflow"
        self._host = os.getenv("TEMPORAL_HOST", "localhost:7233")
        self._enabled = os.getenv("TEMPORAL_SHADOW_MODE", "false").lower() == "true"
        self._initialized = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def initialize(self) -> bool:
        """Attempt to connect to Temporal. Returns True if connected."""
        if not self._enabled:
            return False
        if not TEMPORAL_AVAILABLE:
            logger.info("temporal.shadow_mode.unavailable", reason="temporalio not installed")
            return False
        if self._initialized and self._client:
            return True

        try:
            from temporalio import client as temporal_client
            self._client = await temporal_client.connect(
                self._host,
                namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
            )
            self._initialized = True
            logger.info("temporal.shadow_mode.connected", host=self._host, queue=self._task_queue)
            return True
        except Exception as exc:
            logger.warning("temporal.shadow_mode.connect_failed", error=str(exc)[:160])
            self._initialized = False
            self._client = None
            return False

    async def submit_scheduled_task(self, input_data: AgentTaskInput) -> Optional[str]:
        """Submit a scheduled task to Temporal as a shadow copy. Returns run_id or None."""
        if not self._enabled or not TEMPORAL_AVAILABLE:
            return None

        if self._client is None or not self._initialized:
            connected = await self.initialize()
            if not connected:
                return None

        try:
            assert self._client is not None
            handle = await self._client.start_workflow(
                name=self._workflow_name,
                task=inspect_input(input_data),
                id=f"scheduled-{input_data.schedule_id}-{input_data.execution_id}",
                task_queue=self._task_queue,
                # Let the Redis runtime own execution order; Temporal is advisory
                search_attrs={
                    "ns": input_data.organization_id,
                    "origin": "dual-write-shadow",
                },
            )
            logger.info(
                "temporal.shadow_mode.submitted",
                handle=handle.id,
                thread_id=input_data.thread_id,
                schedule_id=input_data.schedule_id,
            )
            return handle.id
        except Exception as exc:
            logger.warning("temporal.shadow_mode.submit_failed", error=str(exc)[:160])
            return None

    async def get_workflow_status(self, workflow_id: str) -> Optional[str]:
        """Query a Temporal workflow's status."""
        if not self._client or not self._initialized:
            return None
        try:
            handle = self._client.get_workflow_handle(workflow_id)
            result = await handle.describe()
            status = result.status.name
            logger.debug("temporal.workflow.status", id=workflow_id, status=status)
            return status
        except Exception as exc:
            logger.debug("temporal.workflow.status_failed", error=str(exc)[:160])
            return None


# Convert dataclass to dict for Temporal serialization
import dataclasses

def inspect_input(input_data: AgentTaskInput) -> dict[str, Any]:
    return dataclasses.asdict(input_data)


# Module-level singleton
_dual_write: Optional[TemporalDualWrite] = None


def get_temporal_dual_write() -> TemporalDualWrite:
    """Get the singleton Temporal dual-write instance."""
    global _dual_write
    if _dual_write is None:
        _dual_write = TemporalDualWrite()
    return _dual_write
