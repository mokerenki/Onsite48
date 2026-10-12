"""User-scoped REST task APIs for the Onsite48 workspace."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.realtime import record_event
from app.api.websocket_handler import stream_task_events
from app.core.context import onsite48
from app.services.task_execution_control import task_execution_control
from app.core.security import get_current_user, get_db_session
from app.db import get_session_factory
from app.models import OrganizationMember, TaskEvent, TaskStatus, TaskThread, User
from app.services.attachment_store import attachment_store
from app.services.task_persistence import ensure_final_answer_in_conversation
from app.services.report_delivery import prepare_report_delivery
from app.core.task_presentation import concise_task_title, ensure_task_presentation, presentation_from_task, title_for_task
from app.core.workspace_continuity import merge_workspace_context

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskSummary(BaseModel):
    thread_id: str
    title: str
    prompt: str
    domain: str
    status: TaskStatus
    final_answer: Optional[str] = None
    cost_metrics: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    turn_count: int = 1
    presentation: dict[str, str] = Field(default_factory=dict)
    archived: bool = False
    pinned: bool = False
    project_id: Optional[str] = None
    share_token: Optional[str] = None


class TaskDetail(TaskSummary):
    execution_metadata: dict[str, Any]
    events: list[dict[str, Any]]
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class ModifyStepRequest(BaseModel):
    step_index: int = Field(ge=0)
    new_step: dict[str, Any]


class RunTaskRequest(BaseModel):
    """Authenticated REST task contract with the same attachment IDs as WebSocket tasks."""

    message: str = Field(min_length=1, max_length=50_000)
    attachments: list[str] = Field(default_factory=list, max_length=5)
    domain: str = Field(default="general", max_length=64)
    project_id: Optional[str] = Field(default=None, max_length=160)


class RunTaskAccepted(TaskSummary):
    accepted: bool = True


class RenameTaskRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)


class ProjectTaskRequest(BaseModel):
    project_id: Optional[str] = Field(default=None, max_length=160)


async def get_current_membership(session: AsyncSession, user: User) -> OrganizationMember:
    membership = await session.scalar(
        select(OrganizationMember)
        .where(OrganizationMember.user_id == user.id)
        .order_by(OrganizationMember.created_at.asc())
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No active workspace is associated with this account.")
    return membership


async def get_owned_task(session: AsyncSession, user: User, thread_id: str) -> TaskThread:
    membership = await get_current_membership(session, user)
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


async def _run_rest_task(
    *,
    task_id: uuid.UUID,
    thread_id: str,
    execution_prompt: str,
    display_message: str,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> None:
    """Execute a REST-launched task through the canonical agent event stream."""
    session_factory = get_session_factory()
    async for event in stream_task_events(
        execution_prompt,
        str(user_id),
        str(organization_id),
        thread_id,
        onsite48.checkpointer,
        onsite48.model_router,
        onsite48.tool_registry,
        onsite48.domain_router,
        onsite48.browser_service,
        display_message=display_message,
    ):
        async with session_factory() as session:
            task = await session.get(TaskThread, task_id)
            if task is None:
                return
            durable_event, _ = prepare_report_delivery(
                {**event, "thread_id": thread_id},
                organization_id=str(organization_id),
                user_id=str(user_id),
                title=task.title,
            )
            await record_event(session, task, durable_event)
            await session.commit()


def as_summary(task: TaskThread) -> TaskSummary:
    metadata = task.execution_metadata or {}
    return TaskSummary(
        thread_id=task.external_thread_id,
        title=title_for_task(task),
        prompt=task.prompt,
        domain=task.domain,
        status=task.status,
        final_answer=task.final_answer,
        cost_metrics=task.cost_metrics or {},
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed_at=task.completed_at,
        turn_count=int(metadata.get("turn_count", 1)),
        presentation=presentation_from_task(task),
        archived=bool(metadata.get("archived", False)),
        pinned=bool(metadata.get("pinned", False)),
        project_id=str(metadata.get("project_id")) if metadata.get("project_id") else None,
        share_token=str(metadata.get("share_token")) if metadata.get("share_token") else None,
    )


@router.post("/run", response_model=RunTaskAccepted, status_code=status.HTTP_202_ACCEPTED)
async def run_task_rest(
    payload: RunTaskRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> RunTaskAccepted:
    """Launch a durable task without a WebSocket while preserving attachment context."""
    membership = await get_current_membership(session, user)
    prompt = payload.message.strip()
    try:
        attachments, attachment_context = attachment_store.task_context(
            organization_id=str(membership.organization_id),
            user_id=str(user.id),
            attachment_ids=payload.attachments,
        )
    except HTTPException:
        raise

    thread_id = str(uuid.uuid4())
    task = TaskThread(
        external_thread_id=thread_id,
        user_id=user.id,
        organization_id=membership.organization_id,
        title=concise_task_title(prompt),
        prompt=prompt,
        domain=payload.domain.strip() or "general",
        status=TaskStatus.QUEUED,
        # No lease is stamped at acceptance. A lease now means "a consumer has
        # claimed this", and the row itself is the durable work: an accepted
        # task survives the process that accepted it. Stamping a lease here
        # would make the task look claimed for 15 minutes before any consumer
        # could take it, and a BackgroundTask that died with the process would
        # leave a lease nobody could refresh.
        lease_expires_at=None,
        attempt_count=0,
        execution_metadata={
            **ensure_task_presentation({"conversation": [{"turn_id": str(uuid.uuid4()), "role": "user", "content": prompt, "at": datetime.now(timezone.utc).isoformat()}]}, prompt, payload.domain),
            "turn_count": 1,
            "attachments": attachments,
            "launch_channel": "rest",
            "project_id": payload.project_id,
            **merge_workspace_context({}, str(membership.organization_id), str(user.id), payload.project_id),
        },
    )
    session.add(task)
    await session.flush()
    # Deliberately not dispatched here. `BackgroundTasks` run in this process
    # after the response, so a restart between accept and run lost the work
    # entirely and the row could only be failed. The task is now claimed by a
    # queue consumer with FOR UPDATE SKIP LOCKED, so the accepting process
    # exiting is not the end of the request.
    return RunTaskAccepted(**as_summary(task).model_dump(), accepted=True)


@router.get("", response_model=list[TaskSummary])
async def list_tasks(
    limit: int = Query(default=30, ge=1, le=100),
    status_filter: Optional[TaskStatus] = Query(default=None, alias="status"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[TaskSummary]:
    membership = await get_current_membership(session, user)
    query = select(TaskThread).where(
        TaskThread.user_id == user.id,
        TaskThread.organization_id == membership.organization_id,
    )
    if status_filter:
        query = query.where(TaskThread.status == status_filter)
    tasks = (await session.scalars(query.order_by(TaskThread.updated_at.desc()).limit(limit))).all()
    return [as_summary(task) for task in tasks]


@router.patch("/{thread_id}/title", response_model=TaskSummary)
async def rename_task(thread_id: str, payload: RenameTaskRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> TaskSummary:
    task = await get_owned_task(session, user, thread_id)
    task.title = payload.title.strip()
    task.execution_metadata = {**(task.execution_metadata or {}), "title_source": "user"}
    await session.commit()
    await session.refresh(task)
    return as_summary(task)


@router.post("/{thread_id}/archive", response_model=TaskSummary)
async def archive_task(thread_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> TaskSummary:
    task = await get_owned_task(session, user, thread_id)
    task.execution_metadata = {**(task.execution_metadata or {}), "archived": True}
    await session.commit()
    await session.refresh(task)
    return as_summary(task)


@router.post("/{thread_id}/pin", response_model=TaskSummary)
async def pin_task(thread_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> TaskSummary:
    task = await get_owned_task(session, user, thread_id)
    metadata = task.execution_metadata or {}
    task.execution_metadata = {**metadata, "pinned": not bool(metadata.get("pinned", False))}
    await session.commit()
    await session.refresh(task)
    return as_summary(task)


@router.patch("/{thread_id}/project", response_model=TaskSummary)
async def assign_task_project(thread_id: str, payload: ProjectTaskRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> TaskSummary:
    task = await get_owned_task(session, user, thread_id)
    membership = await get_current_membership(session, user)
    task.execution_metadata = merge_workspace_context(
        {**(task.execution_metadata or {}), "project_id": payload.project_id},
        str(membership.organization_id),
        str(user.id),
        payload.project_id,
    )
    await session.commit()
    await session.refresh(task)
    return as_summary(task)


@router.post("/{thread_id}/share", response_model=TaskSummary)
async def share_task(thread_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> TaskSummary:
    task = await get_owned_task(session, user, thread_id)
    token = str(uuid.uuid4())
    task.execution_metadata = {**(task.execution_metadata or {}), "share_token": token, "shared_at": datetime.now(timezone.utc).isoformat()}
    await session.commit()
    await session.refresh(task)
    return as_summary(task)


@router.post("/{thread_id}/duplicate", response_model=TaskSummary, status_code=status.HTTP_201_CREATED)
async def duplicate_task(thread_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> TaskSummary:
    source = await get_owned_task(session, user, thread_id)
    membership = await get_current_membership(session, user)
    new_thread_id = str(uuid.uuid4())
    metadata = dict(source.execution_metadata or {})
    metadata.update({"duplicated_from": source.external_thread_id, "turn_count": 1, "conversation": []})
    metadata = merge_workspace_context(metadata, str(membership.organization_id), str(user.id), metadata.get("project_id"))
    clone = TaskThread(
        external_thread_id=new_thread_id,
        user_id=user.id,
        organization_id=membership.organization_id,
        title=f"Copy of {source.title}"[:240],
        prompt=source.prompt,
        domain=source.domain,
        status=TaskStatus.PAUSED,
        execution_metadata=metadata,
        cost_metrics={},
    )
    session.add(clone)
    await session.commit()
    await session.refresh(clone)
    return as_summary(clone)


@router.get("/{thread_id}", response_model=TaskDetail)
async def get_task(
    thread_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> TaskDetail:
    task = await get_owned_task(session, user, thread_id)
    # Legacy thread records can have durable planner events and a task-level
    # final answer without the assistant turn in execution_metadata. Recover
    # the answer from the task/event record before serializing the transcript.
    event_final_answer = next(
        (
            str(event.payload.get("final_answer"))
            for event in reversed(task.events)
            if event.event_type == "task_complete"
            and isinstance(event.payload, dict)
            and event.payload.get("final_answer")
        ),
        None,
    )
    if not task.final_answer and event_final_answer:
        task.final_answer = event_final_answer
    if ensure_final_answer_in_conversation(task, event_final_answer):
        await session.flush()
    metadata = task.execution_metadata or {}
    artifacts = [
        artifact
        for artifact in (
            list(metadata.get("email_artifacts") or [])
            + list(metadata.get("artifacts") or [])
        )
        if isinstance(artifact, dict) and artifact.get("attachment_id")
    ]
    for event in task.events:
        if event.event_type not in {"task_complete", "artifact_created"}:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        event_artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else []
        if isinstance(payload.get("artifact"), dict):
            event_artifacts = [*event_artifacts, payload["artifact"]]
        for artifact in event_artifacts:
            if not isinstance(artifact, dict) or not artifact.get("attachment_id"):
                continue
            if not any(existing.get("attachment_id") == artifact.get("attachment_id") for existing in artifacts):
                artifacts.append(artifact)
    return TaskDetail(
        **as_summary(task).model_dump(),
        execution_metadata=metadata,
        events=[
            {
                "sequence": event.sequence,
                "type": event.event_type,
                "payload": event.payload,
                "created_at": event.created_at.isoformat(),
            }
            for event in task.events
        ],
        artifacts=artifacts[-20:],
    )


async def _delete_task_checkpoint(thread_id: str) -> None:
    """Best-effort removal of resumable graph state after durable task deletion."""
    delete_thread = getattr(onsite48.checkpointer, "delete_thread", None)
    if callable(delete_thread):
        try:
            await delete_thread(thread_id)
        except Exception:
            # The relational task deletion is authoritative. A later Redis TTL
            # expiry is safe if checkpoint cleanup is temporarily unavailable.
            pass


@router.delete("/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    thread_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """Permanently remove one owned, completed task and its dependent history.

    Only terminal tasks can be removed. In-flight work must be cancelled first
    so a background worker cannot continue writing events to a task the user has
    just removed. Task events and approvals are deleted through SQLAlchemy and
    database foreign-key cascades; Bridge command history is intentionally
    detached by its ``SET NULL`` foreign key for operational audit integrity.
    """
    task = await get_owned_task(session, user, thread_id)
    terminal_statuses = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
    if task.status not in terminal_statuses:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only completed, failed, or cancelled tasks can be deleted. Cancel an active task first.",
        )

    await session.delete(task)
    await session.commit()
    await _delete_task_checkpoint(thread_id)
    await task_execution_control.clear(thread_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{thread_id}/pause", response_model=TaskSummary)
async def pause_task(
    thread_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> TaskSummary:
    task = await get_owned_task(session, user, thread_id)
    if task.status not in {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_FOR_APPROVAL}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Task cannot be paused from {task.status}.")
    task.status = TaskStatus.PAUSED
    task.execution_metadata = {**(task.execution_metadata or {}), "paused_at": datetime.now(timezone.utc).isoformat()}
    try:
        checkpoint = await onsite48.checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
        if checkpoint:
            await onsite48.checkpointer.aput(
                {"configurable": {"thread_id": thread_id}},
                checkpoint.checkpoint,
                {"status": "paused", "paused_at": datetime.now(timezone.utc).isoformat()},
                {},
            )
    except Exception:
        # Durable task status still prevents resumption in the product UI.
        pass
    await session.commit()
    await session.refresh(task)
    return as_summary(task)


@router.post("/{thread_id}/cancel", response_model=TaskSummary)
async def cancel_task(
    thread_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> TaskSummary:
    task = await get_owned_task(session, user, thread_id)
    if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This task is already finished.")
    task.status = TaskStatus.CANCELLED
    task.completed_at = datetime.now(timezone.utc)
    task.execution_metadata = {**(task.execution_metadata or {}), "cancelled_at": task.completed_at.isoformat()}
    await session.commit()
    await session.refresh(task)
    bridge = getattr(request.app.state, "browser_bridge", None)
    cancel_bridge_commands = getattr(bridge, "cancel_commands_for_task", None)
    if callable(cancel_bridge_commands):
        try:
            await cancel_bridge_commands(task_id=task.id, reason="The originating Synth task was cancelled by the user.")
        except Exception:
            # Task cancellation is already durable. A later Bridge lease expiry
            # is still safe if a temporary database failure prevents immediate
            # propagation to the local browser operator.
            pass
    computer = getattr(request.app.state, "agent_computer", None)
    close = getattr(computer, "close", None)
    if callable(close):
        try:
            await close(thread_id)
        except Exception:
            # Cancellation is durable even if a browser context has already
            # crashed or been cleaned by the task runtime.
             pass
    if thread_id:
        try:
            await task_execution_control.signal_cancel(thread_id)
        except Exception:
            pass
    await task_execution_control.clear(thread_id)
    return as_summary(task)


@router.post("/{thread_id}/modify-step", response_model=TaskDetail)
async def modify_task_step(
    thread_id: str,
    payload: ModifyStepRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> TaskDetail:
    task = await get_owned_task(session, user, thread_id)
    if task.status not in {TaskStatus.PAUSED, TaskStatus.WAITING_FOR_APPROVAL, TaskStatus.QUEUED}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only paused or waiting tasks can have their plans modified.")
    checkpoint = await onsite48.checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
    if not checkpoint:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No resumable task state was found.")
    state = checkpoint.checkpoint.get("channel_values", {})
    plan = state.get("plan", [])
    if not isinstance(plan, list) or payload.step_index >= len(plan):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Step index does not exist in the current plan.")
    plan[payload.step_index] = payload.new_step
    await onsite48.checkpointer.aput(
        {"configurable": {"thread_id": thread_id}},
        checkpoint.checkpoint,
        {"plan": plan, "current_step": min(state.get("current_step", 0), payload.step_index)},
        {},
    )
    task.execution_metadata = {**(task.execution_metadata or {}), "plan_last_modified_at": datetime.now(timezone.utc).isoformat()}
    return TaskDetail(
        **as_summary(task).model_dump(),
        execution_metadata=task.execution_metadata,
        events=[{"sequence": event.sequence, "type": event.event_type, "payload": event.payload, "created_at": event.created_at.isoformat()} for event in task.events],
    )