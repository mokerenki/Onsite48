"""Operator workspace API built on Synth's existing task/event contracts."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.realtime import get_owned_task, mark_approval, record_event
from app.core.security import get_current_user, get_db_session
from app.models import OrganizationMember, TaskEvent, TaskStatus, TaskThread, User
from app.services.cost_ledger import load_cost_summary
from app.services.eval_viewer import get_eval_viewer
from app.services.task_persistence import utcnow

router = APIRouter(prefix="/api/operator", tags=["operator"])


class OperatorAction(BaseModel):
    action: Literal["pause", "resume", "approve", "reject"]
    tool_name: str | None = Field(default=None, max_length=128)
    step_index: int | None = Field(default=None, ge=0)
    tool_args: dict[str, Any] | None = Field(default=None)


async def current_organization_id(session: AsyncSession, user: User) -> str:
    """Resolve the caller's workspace.

    Every tenant-scoped read derives the organization from the authenticated
    user rather than accepting it as input. A query parameter here would let any
    authenticated user read another tenant's spend, so the value is never taken
    from the request.
    """
    membership = await session.scalar(
        select(OrganizationMember).where(OrganizationMember.user_id == user.id).order_by(OrganizationMember.created_at.asc())
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="No active workspace is associated with this account.")
    return membership.organization_id


async def owned_task(session: AsyncSession, user: User, thread_id: str) -> TaskThread:
    organization_id = await current_organization_id(session, user)
    task = await get_owned_task(session, thread_id, user.id, organization_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found or access denied.")
    return task


@router.get("/tasks/{thread_id}/feed")
async def task_feed(
    thread_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    task = await owned_task(session, user, thread_id)
    events = list((await session.scalars(
        select(TaskEvent).where(TaskEvent.task_id == task.id).order_by(TaskEvent.sequence.desc()).limit(limit)
    )).all())
    return {
        "thread_id": thread_id,
        "task_title": task.title,
        "task_status": task.status.value,
        "total": len(events),
        "events": [
            {"id": str(event.id), "sequence": event.sequence, "type": event.event_type, "data": event.payload, "timestamp": event.created_at.isoformat()}
            for event in reversed(events)
        ],
    }


@router.get("/tasks/{thread_id}/replay")
async def task_replay(
    thread_id: str,
    failed_only: bool = False,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    task = await owned_task(session, user, thread_id)
    records = get_eval_viewer().replay(thread_id, failed_only=failed_only)
    return {"thread_id": thread_id, "task_title": task.title, "task_status": task.status.value, "failed_only": failed_only, "steps": records, "total": len(records)}


@router.post("/tasks/{thread_id}/control")
async def task_control(
    thread_id: str,
    payload: OperatorAction,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    task = await owned_task(session, user, thread_id)
    now = utcnow().isoformat()
    if payload.action == "pause":
        if task.status not in {TaskStatus.RUNNING, TaskStatus.WAITING_FOR_APPROVAL}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only a running task can be paused.")
        task.status = TaskStatus.PAUSED
        event = {"type": "task_paused", "thread_id": thread_id, "timestamp": now, "reason": "operator"}
    elif payload.action == "resume":
        if task.status != TaskStatus.PAUSED:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only a paused task can be resumed.")
        task.status = TaskStatus.RUNNING
        event = {"type": "task_resumed", "thread_id": thread_id, "timestamp": now, "reason": "operator"}
    elif payload.action in {"approve", "reject"}:
        if not payload.tool_name or payload.step_index is None:
            raise HTTPException(status_code=422, detail="tool_name and step_index are required for approval decisions.")
        approval = await mark_approval(
            session,
            task,
            payload.tool_name,
            payload.step_index,
            payload.action == "approve",
            user.id,
            tool_args=payload.tool_args,
        )
        event = {"type": "approval_decided", "thread_id": thread_id, "tool_name": approval.tool_name, "step_index": approval.step_index, "approved": approval.decision, "timestamp": now}
    else:
        raise HTTPException(status_code=422, detail="Unsupported operator action.")
    await record_event(session, task, event)
    await session.commit()
    return {"thread_id": thread_id, "task_status": task.status.value, "event": event}


@router.get("/costs")
async def cost_summary(
    since: datetime | None = Query(default=None, description="Inclusive lower bound on task creation time."),
    until: datetime | None = Query(default=None, description="Exclusive upper bound on task creation time."),
    limit: int = Query(default=1000, ge=1, le=20000, description="Maximum tasks scanned. A truncated answer is flagged, not hidden."),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Spend for the caller's own workspace over a window.

    There is deliberately no `organization_id` parameter. The workspace is
    resolved from the authenticated user, because an operator able to name any
    organization would be able to read any organization's spend.

    `total_cost_usd_is_lower_bound` is the field to read first. Spend for models
    with no configured price is not estimated and not folded into the total, so
    a workspace that has run unpriced models reports a number that is a floor
    rather than a measurement, and says so.
    """
    if since is not None and until is not None and since > until:
        raise HTTPException(status_code=422, detail="`since` must not be later than `until`.")
    organization_id = await current_organization_id(session, user)
    summary = await load_cost_summary(
        session,
        organization_id=organization_id,
        since=since,
        until=until,
        limit=limit,
    )
    return {"organization_id": organization_id, **summary}