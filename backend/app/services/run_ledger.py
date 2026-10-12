"""Durable run and attempt ledger helpers.

The graph remains Redis-checkpointed for speed; these records provide the
relational audit trail needed for restart, approval, and post-run analysis.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentArtifact, AgentEvidence, AgentRun, AgentStepAttempt, TaskThread


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def idempotency_key(run_id: UUID | str, step_id: str, tool_name: str, arguments: dict[str, Any]) -> str:
    """Identify one action within a run independently of plan position or ID."""
    payload = json.dumps({"run_id": str(run_id), "tool": tool_name, "arguments": arguments}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


async def start_run(session: AsyncSession, task: TaskThread, *, intent: dict[str, Any] | None = None, plan: list[dict[str, Any]] | None = None) -> AgentRun:
    # Serialize allocation for one task so concurrent workers cannot observe
    # the same MAX(run_number) and create duplicate run identities.
    await session.scalar(select(TaskThread).where(TaskThread.id == task.id).with_for_update())
    latest = await session.scalar(select(func.max(AgentRun.run_number)).where(AgentRun.task_id == task.id))
    run = AgentRun(task_id=task.id, run_number=int(latest or 0) + 1, status="running", intent=intent or {}, plan=plan or [], started_at=utcnow())
    session.add(run)
    await session.flush()
    return run


async def record_attempt(session: AsyncSession, run: AgentRun, *, step_id: str, tool_name: str, arguments: dict[str, Any], attempt_number: int = 1, status: str = "started", result: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> AgentStepAttempt:
    key = idempotency_key(run.id, step_id, tool_name, arguments)
    existing = await session.scalar(select(AgentStepAttempt).where(AgentStepAttempt.run_id == run.id, AgentStepAttempt.idempotency_key == key))
    if existing:
        return existing
    attempt = AgentStepAttempt(run_id=run.id, step_id=step_id, attempt_number=attempt_number, idempotency_key=key, tool_name=tool_name, arguments=arguments, status=status, result=result, error=error, completed_at=utcnow() if status in {"completed", "failed"} else None)
    session.add(attempt)
    await session.flush()
    return attempt


async def add_evidence(session: AsyncSession, run: AgentRun, *, content: str, step_id: str | None = None, source_url: str | None = None, title: str | None = None, evidence_metadata: dict[str, Any] | None = None) -> AgentEvidence:
    evidence = AgentEvidence(run_id=run.id, step_id=step_id, source_url=source_url, title=title, content=content, evidence_metadata=evidence_metadata or {})
    session.add(evidence)
    await session.flush()
    return evidence


async def add_artifact(session: AsyncSession, run: AgentRun, *, filename: str, media_type: str, storage_key: str, checksum: str, provenance: dict[str, Any] | None = None, logical_id: str | None = None) -> AgentArtifact:
    existing = await session.scalar(select(AgentArtifact).where(AgentArtifact.run_id == run.id, AgentArtifact.storage_key == storage_key))
    if existing:
        return existing
    logical = logical_id or storage_key
    previous = await session.scalar(select(AgentArtifact).where(AgentArtifact.logical_id == logical).order_by(AgentArtifact.version.desc()))
    artifact = AgentArtifact(
        run_id=run.id,
        filename=filename,
        media_type=media_type,
        storage_key=storage_key,
        checksum=checksum,
        logical_id=logical,
        version=(previous.version + 1) if previous else 1,
        parent_artifact_id=previous.id if previous else None,
        provenance={**(provenance or {}), "logical_id": logical, "version": (previous.version + 1) if previous else 1},
    )
    session.add(artifact)
    await session.flush()
    return artifact