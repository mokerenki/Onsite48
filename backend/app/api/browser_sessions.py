from __future__ import annotations
import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import get_current_user, get_db_session
from app.models import OrganizationMember, TaskThread, User
from app.services.browser_session import summary

router = APIRouter(prefix="/api/browser-sessions", tags=["browser-sessions"])

class CreateRequest(BaseModel):
    task_id: uuid.UUID | None = None
    mode: str = Field(default="task_workspace", pattern="^(focused_tab|task_tab|task_workspace)$")
    requested_origins: list[str] = Field(default_factory=list, max_length=20)
    requested_capabilities: list[str] = Field(default_factory=list, max_length=20)
class AuthorizeRequest(BaseModel):
    device_id: uuid.UUID
    origins: list[str] = Field(default_factory=list, max_length=20)
    capabilities: list[str] = Field(default_factory=list, max_length=20)
    tab_ids: list[int] = Field(default_factory=list, max_length=5)
    primary_tab_id: int | None = Field(default=None, ge=0)
class HeartbeatRequest(BaseModel):
    step_id: str | None = Field(default=None, max_length=96)
    url: str | None = Field(default=None, max_length=2048)
    observation: dict[str, Any] | None = None

async def member(db: AsyncSession, user: User) -> OrganizationMember:
    value = await db.scalar(select(OrganizationMember).where(OrganizationMember.user_id == user.id).order_by(OrganizationMember.created_at.asc()))
    if value is None: raise HTTPException(403, "No active workspace is associated with this account.")
    return value
async def owned(request: Request, sid: uuid.UUID, user: User, db: AsyncSession):
    m = await member(db, user); item = await request.app.state.browser_sessions.get(session_id=sid, organization_id=m.organization_id, user_id=user.id)
    if item is None: raise HTTPException(404, "Browser session not found.")
    return m, item

def fail(exc: Exception) -> HTTPException: return HTTPException(409, str(exc))

@router.post("", status_code=201)
async def create(payload: CreateRequest, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    m = await member(db, user)
    if payload.task_id is not None:
        task = await db.scalar(select(TaskThread).where(TaskThread.id == payload.task_id, TaskThread.user_id == user.id, TaskThread.organization_id == m.organization_id))
        if task is None:
            raise HTTPException(404, "Task not found.")
    try:
        item = await request.app.state.browser_sessions.create(organization_id=m.organization_id, user_id=user.id, task_id=payload.task_id, mode=payload.mode, origins=payload.requested_origins, capabilities=payload.requested_capabilities)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"session": summary(item)}
@router.get("/{sid}")
async def get(sid: uuid.UUID, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    _, item = await owned(request, sid, user, db); return {"session": summary(item)}
@router.post("/{sid}/authorize")
async def authorize(sid: uuid.UUID, payload: AuthorizeRequest, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    m = await member(db, user)
    try: item = await request.app.state.browser_sessions.authorize(session_id=sid, organization_id=m.organization_id, user_id=user.id, device_id=payload.device_id, origins=payload.origins, capabilities=payload.capabilities, tab_ids=payload.tab_ids, primary_tab_id=payload.primary_tab_id)
    except (LookupError, ValueError) as exc: raise fail(exc) from exc
    return {"session": summary(item)}
@router.post("/{sid}/heartbeat")
async def heartbeat(sid: uuid.UUID, payload: HeartbeatRequest, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    m = await member(db, user)
    try: item = await request.app.state.browser_sessions.heartbeat(session_id=sid, organization_id=m.organization_id, user_id=user.id, step_id=payload.step_id, url=payload.url, observation=payload.observation)
    except (LookupError, ValueError) as exc: raise fail(exc) from exc
    return {"session": summary(item)}
async def change(sid, status, request, user, db):
    m = await member(db, user)
    try: item = await request.app.state.browser_sessions.transition(session_id=sid, organization_id=m.organization_id, user_id=user.id, status=status, reason=f"User requested {status}.")
    except (LookupError, ValueError) as exc: raise fail(exc) from exc
    return {"session": summary(item)}
@router.post("/{sid}/pause")
async def pause(sid: uuid.UUID, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)): return await change(sid, "paused", request, user, db)
@router.post("/{sid}/takeover")
async def takeover(sid: uuid.UUID, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)): return await change(sid, "takeover", request, user, db)
@router.post("/{sid}/resume")
async def resume(sid: uuid.UUID, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)): return await change(sid, "resumed", request, user, db)
@router.post("/{sid}/stop")
async def stop(sid: uuid.UUID, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)): return await change(sid, "cancelled", request, user, db)
@router.get("/{sid}/events")
async def events(sid: uuid.UUID, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    m, _ = await owned(request, sid, user, db); values = await request.app.state.browser_sessions.events(session_id=sid, organization_id=m.organization_id, user_id=user.id); return {"events": [{"sequence": x.sequence, "type": x.event_type, "payload": x.payload, "created_at": x.created_at.isoformat()} for x in values]}