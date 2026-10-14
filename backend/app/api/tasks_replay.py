"""Time-Travel Debugger API — state snapshots and replay for completed tasks.

Exposes:
  GET /api/tasks/{thread_id}/replay?step=N      State snapshot at checkpoint N
  GET /api/tasks/{thread_id}/checkpoints          List all checkpoints for a thread
  GET /api/tasks/{thread_id}/replay/state?step=N  Alias for replay with step
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
import structlog

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.context import onsite48
from app.core.checkpoint_manager import CheckpointManager
from app.core.security import get_current_user, get_db_session
from app.db import get_session_factory
from app.models import OrganizationMember, TaskThread, User, TaskEvent
from app.runtime.journal import Journal, journal as default_journal

logger = structlog.get_logger("onsite48.api.tasks_replay")
router = APIRouter(prefix="/api/tasks", tags=["replay"])


async def _get_owned_thread(
    thread_id: str,
    user: User,
    session: AsyncSession,
) -> TaskThread:
    membership = await session.scalar(
        select(OrganizationMember)
        .where(OrganizationMember.user_id == user.id)
        .order_by(OrganizationMember.created_at.asc())
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No workspace associated with this account.")
    task = await session.scalar(
        select(TaskThread)
        .where(
            TaskThread.external_thread_id == thread_id,
            TaskThread.user_id == user.id,
            TaskThread.organization_id == membership.organization_id,
        )
        .options(selectinload(TaskThread.events))
    )
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
    return task


@router.get("/{thread_id}/checkpoints", response_model=list[dict[str, Any]])
async def list_checkpoints(
    thread_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    limit: int = Query(default=50, ge=1, le=500),
):
    """List LangGraph checkpoints stored for a thread, newest first."""
    await _get_owned_thread(thread_id, user, session)

    checkpointer = onsite48.checkpointer
    if checkpointer is None:
        raise HTTPException(status_code=503, detail="Checkpointer not available")

    config = {"configurable": {"thread_id": thread_id}}
    checkpoints: list[dict[str, Any]] = []
    try:
        async for tuple_obj in checkpointer.alist(config=config, limit=limit):
            cp = tuple_obj.checkpoint if isinstance(tuple_obj, dict) else getattr(tuple_obj, "checkpoint", {})
            metadata = tuple_obj.metadata if isinstance(tuple_obj, dict) else getattr(tuple_obj, "metadata", {})
            checkpoints.append({
                "checkpoint_id": cp.get("id") or cp.get("ts"),
                "checkpoint_ns": config.get("configurable", {}).get("checkpoint_ns", ""),
                "created_at": cp.get("ts"),
                "metadata": metadata,
                "step": metadata.get("step", 0) if isinstance(metadata, dict) else 0,
            })
    except Exception as exc:
        logger.error("replay.list_checkpoints_failed", thread_id=thread_id, error=str(exc)[:200])
        raise HTTPException(status_code=500, detail=f"Failed to list checkpoints: {exc}")

    checkpoints.sort(key=lambda x: x.get("checkpoint_id", ""), reverse=True)
    return checkpoints


@router.get("/{thread_id}/replay")
async def get_replay_snapshot(
    thread_id: str,
    step: int = Query(default=0, ge=0, description="Checkpoint step index (0 = earliest)"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Return the agent state snapshot at a specific checkpoint step (Time-Travel Debugger)."""
    task = await _get_owned_thread(thread_id, user, session)

    checkpointer = onsite48.checkpointer
    if checkpointer is None:
        raise HTTPException(status_code=503, detail="Checkpointer not available")

    config = {"configurable": {"thread_id": thread_id}}

    try:
        all_checkpoints = []
        async for tuple_obj in checkpointer.alist(config=config):
            cp = tuple_obj.checkpoint if isinstance(tuple_obj, dict) else getattr(tuple_obj, "checkpoint", {})
            metadata = tuple_obj.metadata if isinstance(tuple_obj, dict) else getattr(tuple_obj, "metadata", {})
            all_checkpoints.append((tuple_obj, cp, metadata))

        all_checkpoints.sort(key=lambda x: x[1].get("id", x[1].get("ts", "")), reverse=True)
        if not all_checkpoints:
            raise HTTPException(status_code=404, detail="No checkpoints found for this task")

        if step > 0:
            target = all_checkpoints[min(step, len(all_checkpoints) - 1)]
        else:
            target = all_checkpoints[-1]

        tuple_obj, cp, metadata = target
        channel_values = cp.get("channel_values", {}) if isinstance(cp, dict) else {}

        snapshot = {
            "thread_id": thread_id,
            "task_title": task.title or "",
            "checkpoint": {
                "id": cp.get("id") or cp.get("ts"),
                "ts": cp.get("ts"),
                "step": metadata.get("step", 0) if isinstance(metadata, dict) else 0,
            },
            "state": {
                "goal": channel_values.get("goal", ""),
                "plan": channel_values.get("plan", []),
                "results": channel_values.get("results", []),
                "current_step": channel_values.get("current_step", 0),
                "messages": channel_values.get("messages", []),
                "status": channel_values.get("status", "unknown"),
                "error": channel_values.get("error"),
                "token_count": channel_values.get("token_count", 0),
                "cost_usd": channel_values.get("cost_usd", 0.0),
            },
            "event_count": len(task.events) if task.events else 0,
        }

        return snapshot
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("replay.snapshot_failed", thread_id=thread_id, step=step, error=str(exc)[:200])
        raise HTTPException(status_code=500, detail=f"Failed to get replay snapshot: {exc}")


@router.get("/{thread_id}/events", response_model=list[dict[str, Any]])
async def get_task_events(
    thread_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    limit: int = Query(default=500, ge=1, le=2000),
):
    """Return the durable event log for a task (from TaskEvent / journal)."""
    task = await _get_owned_thread(thread_id, user, session)

    events: list[dict[str, Any]] = []

    # Try the durable journal (RunEvent table) first
    session_factory = get_session_factory()
    j = Journal(session_factory=session_factory)
    try:
        run_id = task.execution_metadata.get("run_id") if task.execution_metadata else None
        if run_id:
            journal_events = await j.list_events(run_id)
            for ev in journal_events:
                events.append({
                    "seq": ev.get("seq", 0),
                    "type": ev.get("type", ""),
                    "payload": ev.get("payload", {}),
                    "timestamp": ev.get("created_at"),
                })
    except Exception:
        pass

    # Fall back to TaskEvent if no journal events
    if not events and task.events:
        for ev in task.events:
            events.append({
                "seq": 0,
                "type": ev.event_type,
                "payload": ev.payload or {},
                "timestamp": ev.created_at.isoformat() if ev.created_at else None,
            })

    # If still nothing, try Redis history store
    if not events:
        from app.core.history_store import HistoryStore
        history = HistoryStore()
        timeline = await history.get_task_timeline(thread_id)
        for i, event in enumerate(timeline):
            events.append({
                "seq": i,
                "type": event.get("type", ""),
                "payload": event.get("data", {}),
                "timestamp": event.get("timestamp"),
            })

    events.sort(key=lambda e: e.get("seq", 0))
    return events[:limit]
