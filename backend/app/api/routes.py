"""API routes — tasks, runs, approvals, replay."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from app.services.task_runner import enqueue_run, get_run, list_events, resume_run

router = APIRouter()


class CreateTaskRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=20_000)
    org_id: str | None = None
    user_id: str | None = None
    role: str = "loop"
    max_steps: int | None = None
    max_cost_usd: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateTaskResponse(BaseModel):
    task_id: str
    run_id: str
    status: str


class ApprovalRequest(BaseModel):
    approved: bool
    note: str | None = None


@router.post("/tasks", response_model=CreateTaskResponse)
async def create_task(body: CreateTaskRequest, background: BackgroundTasks):
    task_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    background.add_task(
        enqueue_run,
        run_id=run_id,
        task_id=task_id,
        goal=body.goal,
        org_id=body.org_id,
        user_id=body.user_id,
        role=body.role,
        max_steps=body.max_steps,
        max_cost_usd=body.max_cost_usd,
        metadata=body.metadata,
    )
    return CreateTaskResponse(task_id=task_id, run_id=run_id, status="queued")


@router.get("/runs/{run_id}")
async def get_run_status(run_id: str):
    state = await get_run(run_id)
    if state is None:
        raise HTTPException(404, "run not found")
    return state


@router.get("/runs/{run_id}/events")
async def get_run_events(run_id: str):
    events = await list_events(run_id)
    return {"run_id": run_id, "events": events}


@router.post("/runs/{run_id}/approve")
async def approve_run(run_id: str, body: ApprovalRequest, background: BackgroundTasks):
    state = await get_run(run_id)
    if state is None:
        raise HTTPException(404, "run not found")
    if not state.get("approval_pending"):
        raise HTTPException(400, "no approval pending")
    background.add_task(resume_run, run_id=run_id, approved=body.approved, note=body.note)
    return {"run_id": run_id, "status": "resuming"}


@router.get("/health")
async def api_health():
    return {"ok": True}
