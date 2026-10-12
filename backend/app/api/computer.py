"""User-scoped controls for the isolated task browser computer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.realtime import record_event
from app.core.context import onsite48
from app.core.security import get_current_user, get_db_session
from app.models import OrganizationMember, TaskStatus, TaskThread, User
from app.services.agent_computer import AgentComputerUserTakeoverActive, UnsafeBrowserTarget
from app.services.task_execution_control import task_execution_control

router = APIRouter(prefix="/api/computer", tags=["agent-computer"])


class NavigateRequest(BaseModel):
    url: HttpUrl


class ScrollRequest(BaseModel):
    direction: str = Field(pattern="^(up|down)$")
    amount: int | None = Field(default=None, ge=50, le=10_000)


class CoordinateRequest(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    normalized: bool = True


class UserTypeRequest(BaseModel):
    # Do not put a length constraint here: FastAPI's validation error payload can
    # echo an invalid credential. The service validates and returns only a
    # generic error, while successful responses return text_length only.
    text: str


class UserKeyRequest(BaseModel):
    key: str


async def owned_thread(session: AsyncSession, user: User, thread_id: str) -> TaskThread:
    membership = await session.scalar(
        select(OrganizationMember).where(OrganizationMember.user_id == user.id).order_by(OrganizationMember.created_at.asc())
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No active workspace is associated with this account.")
    task = await session.scalar(
        select(TaskThread).where(
            TaskThread.external_thread_id == thread_id,
            TaskThread.user_id == user.id,
            TaskThread.organization_id == membership.organization_id,
        )
    )
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
    return task


async def _checkpoint_control_update(thread_id: str, control: dict[str, Any]) -> None:
    """Best-effort graph checkpoint marker for a live user-control transition."""
    try:
        checkpoint = await onsite48.checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
        if checkpoint:
            await onsite48.checkpointer.aput(
                {"configurable": {"thread_id": thread_id}},
                checkpoint.checkpoint,
                {
                    "status": "paused",
                    "execution_state": "paused",
                    "paused_at": control["changed_at"],
                    "computer_control": control,
                },
                {},
            )
    except Exception:
        # TaskThread status and events remain authoritative if Redis/checkpoint
        # persistence has a transient fault. Do not fail a user-held session.
        return


def _control_metadata(mode: str) -> dict[str, str]:
    return {"mode": mode, "changed_at": datetime.now(timezone.utc).isoformat()}


def _computer(request: Request) -> Any:
    computer = getattr(request.app.state, "agent_computer", None)
    if computer is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="The Agent Computer is not available.")
    return computer


def _translate_computer_error(exc: Exception) -> HTTPException:
    if isinstance(exc, UnsafeBrowserTarget):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    if isinstance(exc, AgentComputerUserTakeoverActive):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/{thread_id}/control")
async def task_computer_control_state(
    thread_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    task = await owned_thread(session, user, thread_id)
    state = await _computer(request).control_state(thread_id)
    metadata = dict(task.execution_metadata or {})
    return {"thread_id": thread_id, "task_status": task.status, "durable_control": metadata.get("computer_control"), **state}


@router.post("/{thread_id}/takeover")
async def begin_task_computer_takeover(
    thread_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    task = await owned_thread(session, user, thread_id)
    if task.status not in {TaskStatus.RUNNING, TaskStatus.PAUSED}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Agent Computer takeover is available only for a running or paused task.")
    try:
        snapshot = await _computer(request).begin_user_takeover(thread_id)
    except Exception as exc:
        raise _translate_computer_error(exc) from exc

    control = _control_metadata("user")
    control["generation"] = str(await task_execution_control.start_user_takeover(thread_id))
    task.status = TaskStatus.PAUSED
    task.execution_metadata = {**(task.execution_metadata or {}), "computer_control": control}
    await _checkpoint_control_update(thread_id, control)
    await record_event(session, task, {
        "type": "agent_computer_takeover_started",
        "thread_id": thread_id,
        "mode": "user",
        "timestamp": control["changed_at"],
    })
    await session.commit()
    return {"control": control, "task_status": task.status, **snapshot}


@router.post("/{thread_id}/return")
async def return_task_computer_to_synth(
    thread_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    task = await owned_thread(session, user, thread_id)
    if task.status != TaskStatus.PAUSED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Pause the task before returning browser control to Synth.")
    try:
        snapshot = await _computer(request).return_to_agent(thread_id)
    except Exception as exc:
        raise _translate_computer_error(exc) from exc

    control = _control_metadata("agent_ready")
    control["generation"] = str((await task_execution_control.return_to_agent_ready(thread_id)).generation)
    task.execution_metadata = {**(task.execution_metadata or {}), "computer_control": control}
    await _checkpoint_control_update(thread_id, control)
    await record_event(session, task, {
        "type": "agent_computer_takeover_returned",
        "thread_id": thread_id,
        "mode": "agent_ready",
        "timestamp": control["changed_at"],
    })
    await session.commit()
    return {"control": control, "task_status": task.status, "resume_required": True, **snapshot}


@router.post("/{thread_id}/navigate")
async def navigate_task_computer(
    thread_id: str,
    payload: NavigateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    await owned_thread(session, user, thread_id)
    try:
        return await _computer(request).navigate(thread_id, str(payload.url))
    except Exception as exc:
        raise _translate_computer_error(exc) from exc


@router.post("/{thread_id}/reload")
async def reload_task_computer(
    thread_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    await owned_thread(session, user, thread_id)
    try:
        return await _computer(request).reload(thread_id)
    except Exception as exc:
        raise _translate_computer_error(exc) from exc


@router.post("/{thread_id}/back")
async def go_back_task_computer(
    thread_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    await owned_thread(session, user, thread_id)
    try:
        return await _computer(request).go_back(thread_id)
    except Exception as exc:
        raise _translate_computer_error(exc) from exc


@router.post("/{thread_id}/scroll")
async def scroll_task_computer(
    thread_id: str,
    payload: ScrollRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    await owned_thread(session, user, thread_id)
    try:
        return await _computer(request).scroll(thread_id, payload.direction, payload.amount)
    except Exception as exc:
        raise _translate_computer_error(exc) from exc


@router.post("/{thread_id}/user/navigate")
async def user_navigate_task_computer(
    thread_id: str,
    payload: NavigateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    await owned_thread(session, user, thread_id)
    try:
        return await _computer(request).user_navigate(thread_id, str(payload.url))
    except Exception as exc:
        raise _translate_computer_error(exc) from exc


@router.post("/{thread_id}/user/reload")
async def user_reload_task_computer(
    thread_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    await owned_thread(session, user, thread_id)
    try:
        return await _computer(request).user_reload(thread_id)
    except Exception as exc:
        raise _translate_computer_error(exc) from exc


@router.post("/{thread_id}/user/back")
async def user_back_task_computer(
    thread_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    await owned_thread(session, user, thread_id)
    try:
        return await _computer(request).user_go_back(thread_id)
    except Exception as exc:
        raise _translate_computer_error(exc) from exc


@router.post("/{thread_id}/user/scroll")
async def user_scroll_task_computer(
    thread_id: str,
    payload: ScrollRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    await owned_thread(session, user, thread_id)
    try:
        return await _computer(request).user_scroll(thread_id, payload.direction, payload.amount)
    except Exception as exc:
        raise _translate_computer_error(exc) from exc


@router.post("/{thread_id}/user/click")
async def user_click_task_computer(
    thread_id: str,
    payload: CoordinateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    await owned_thread(session, user, thread_id)
    try:
        return await _computer(request).user_click_at(thread_id, payload.x, payload.y, payload.normalized)
    except Exception as exc:
        raise _translate_computer_error(exc) from exc


@router.post("/{thread_id}/user/type")
async def user_type_task_computer(
    thread_id: str,
    payload: UserTypeRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    await owned_thread(session, user, thread_id)
    try:
        # The user first selects the page field directly. This request types into
        # that focused field only; it never sends a second coordinate click that
        # could redirect a password or MFA value to a different control. The text
        # is intentionally absent from the response and never recorded as an event.
        return await _computer(request).user_type_focused(thread_id, payload.text)
    except Exception as exc:
        raise _translate_computer_error(exc) from exc


@router.post("/{thread_id}/user/key")
async def user_key_task_computer(
    thread_id: str,
    payload: UserKeyRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    await owned_thread(session, user, thread_id)
    try:
        return await _computer(request).user_press_key(thread_id, payload.key)
    except Exception as exc:
        raise _translate_computer_error(exc) from exc


@router.get("/{thread_id}/snapshot")
async def task_computer_snapshot(
    thread_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    await owned_thread(session, user, thread_id)
    try:
        return await _computer(request).snapshot(thread_id)
    except Exception as exc:
        raise _translate_computer_error(exc) from exc


@router.get("/{thread_id}/inspect")
async def inspect_task_computer(
    thread_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    await owned_thread(session, user, thread_id)
    try:
        return await _computer(request).inspect(thread_id)
    except Exception as exc:
        raise _translate_computer_error(exc) from exc


@router.get("/{thread_id}/read")
async def read_task_computer(
    thread_id: str,
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20_000, ge=1, le=30_000),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    await owned_thread(session, user, thread_id)
    try:
        return await _computer(request).read_page(thread_id, offset=offset, limit=limit)
    except Exception as exc:
        raise _translate_computer_error(exc) from exc