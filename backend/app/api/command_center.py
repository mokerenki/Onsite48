"""Collaborative Command Center API for organization-scoped work."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.product_tasks import get_current_membership
from app.core.security import get_current_user, get_db_session
from app.models import CommandProject, CommandWorkItem, OrganizationMember, User, WorkItemComment
from app.services.notifications import create_notification

router = APIRouter(prefix="/api/command-center", tags=["command-center"])


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=6000)


class WorkItemCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=8000)
    assignee_user_id: uuid.UUID | None = None
    priority: str = Field(default="medium", pattern="^(low|medium|high|urgent)$")
    due_at: datetime | None = None
    task_id: uuid.UUID | None = None
    proposal_id: uuid.UUID | None = None


class WorkItemUpdateRequest(BaseModel):
    status: str | None = Field(default=None, pattern="^(todo|in_progress|waiting_approval|done|blocked)$")
    assignee_user_id: uuid.UUID | None = None
    priority: str | None = Field(default=None, pattern="^(low|medium|high|urgent)$")
    due_at: datetime | None = None


class CommentCreateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=8000)


async def project_for_org(session: AsyncSession, organization_id: uuid.UUID, project_id: uuid.UUID) -> CommandProject:
    project = await session.scalar(select(CommandProject).where(CommandProject.id == project_id, CommandProject.organization_id == organization_id))
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Command Center project not found.")
    return project


async def work_item_for_org(session: AsyncSession, organization_id: uuid.UUID, item_id: uuid.UUID) -> CommandWorkItem:
    item = await session.scalar(select(CommandWorkItem).where(CommandWorkItem.id == item_id, CommandWorkItem.organization_id == organization_id))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Command Center work item not found.")
    return item


async def validate_assignee(session: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID | None) -> None:
    if user_id is None:
        return
    member = await session.scalar(select(OrganizationMember).where(OrganizationMember.organization_id == organization_id, OrganizationMember.user_id == user_id))
    if member is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The assignee is not part of this organization.")


def work_item_response(item: CommandWorkItem, comments: list[WorkItemComment] | None = None) -> dict[str, Any]:
    return {
        "id": str(item.id), "project_id": str(item.project_id), "title": item.title, "description": item.description,
        "status": item.status, "priority": item.priority, "assignee_user_id": str(item.assignee_user_id) if item.assignee_user_id else None,
        "task_id": str(item.task_id) if item.task_id else None, "proposal_id": str(item.proposal_id) if item.proposal_id else None,
        "due_at": item.due_at.isoformat() if item.due_at else None, "created_at": item.created_at.isoformat(), "updated_at": item.updated_at.isoformat(),
        "comments": [{"id": str(comment.id), "author_user_id": str(comment.author_user_id), "body": comment.body, "created_at": comment.created_at.isoformat()} for comment in (comments or [])],
    }


def project_response(project: CommandProject, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": str(project.id), "name": project.name, "description": project.description, "status": project.status, "created_at": project.created_at.isoformat(), "updated_at": project.updated_at.isoformat(), "items": items}


@router.get("/members")
async def list_members(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)):
    membership = await get_current_membership(session, user)
    rows = (await session.execute(select(User, OrganizationMember).join(OrganizationMember, OrganizationMember.user_id == User.id).where(OrganizationMember.organization_id == membership.organization_id))).all()
    return {"members": [{"id": str(member.id), "name": member.display_name, "email": member.email, "role": org_member.role.value} for member, org_member in rows]}


@router.get("/projects")
async def list_projects(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)):
    membership = await get_current_membership(session, user)
    projects = list((await session.scalars(select(CommandProject).where(CommandProject.organization_id == membership.organization_id).order_by(CommandProject.updated_at.desc()))).all())
    items = list((await session.scalars(select(CommandWorkItem).where(CommandWorkItem.organization_id == membership.organization_id).order_by(CommandWorkItem.updated_at.desc()))).all())
    comments = list((await session.scalars(select(WorkItemComment).where(WorkItemComment.work_item_id.in_([item.id for item in items] or [uuid.uuid4()])))).all())
    comments_by_item: dict[uuid.UUID, list[WorkItemComment]] = {}
    for comment in comments: comments_by_item.setdefault(comment.work_item_id, []).append(comment)
    items_by_project: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for item in items: items_by_project.setdefault(item.project_id, []).append(work_item_response(item, comments_by_item.get(item.id)))
    return {"projects": [project_response(project, items_by_project.get(project.id, [])) for project in projects]}


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectCreateRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)):
    membership = await get_current_membership(session, user)
    project = CommandProject(organization_id=membership.organization_id, created_by_user_id=user.id, name=payload.name.strip(), description=(payload.description or "").strip() or None)
    session.add(project); await session.flush()
    return project_response(project, [])


@router.post("/projects/{project_id}/items", status_code=status.HTTP_201_CREATED)
async def create_work_item(project_id: uuid.UUID, payload: WorkItemCreateRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)):
    membership = await get_current_membership(session, user)
    await project_for_org(session, membership.organization_id, project_id)
    await validate_assignee(session, membership.organization_id, payload.assignee_user_id)
    item = CommandWorkItem(project_id=project_id, organization_id=membership.organization_id, created_by_user_id=user.id, assignee_user_id=payload.assignee_user_id, task_id=payload.task_id, proposal_id=payload.proposal_id, title=payload.title.strip(), description=(payload.description or "").strip() or None, priority=payload.priority, due_at=payload.due_at)
    session.add(item); await session.flush()
    if item.assignee_user_id and item.assignee_user_id != user.id:
        await create_notification(session, organization_id=membership.organization_id, user_id=item.assignee_user_id, notification_type="work_assigned", title="New Command Center assignment", body=item.title, href="/command-center", metadata={"work_item_id": str(item.id), "project_id": str(project_id)})
    return work_item_response(item)


@router.patch("/items/{item_id}")
async def update_work_item(item_id: uuid.UUID, payload: WorkItemUpdateRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)):
    membership = await get_current_membership(session, user)
    item = await work_item_for_org(session, membership.organization_id, item_id)
    fields = payload.model_fields_set
    if "assignee_user_id" in fields:
        await validate_assignee(session, membership.organization_id, payload.assignee_user_id)
        item.assignee_user_id = payload.assignee_user_id
        if item.assignee_user_id and item.assignee_user_id != user.id:
            await create_notification(session, organization_id=membership.organization_id, user_id=item.assignee_user_id, notification_type="work_assigned", title="Command Center work assigned to you", body=item.title, href="/command-center", metadata={"work_item_id": str(item.id), "project_id": str(item.project_id)})
    if "status" in fields: item.status = payload.status or item.status
    if "priority" in fields: item.priority = payload.priority or item.priority
    if "due_at" in fields: item.due_at = payload.due_at
    await session.flush()
    return work_item_response(item)


@router.post("/items/{item_id}/comments", status_code=status.HTTP_201_CREATED)
async def add_comment(item_id: uuid.UUID, payload: CommentCreateRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)):
    membership = await get_current_membership(session, user)
    await work_item_for_org(session, membership.organization_id, item_id)
    comment = WorkItemComment(work_item_id=item_id, author_user_id=user.id, body=payload.body.strip())
    session.add(comment); await session.flush()
    return {"id": str(comment.id), "author_user_id": str(comment.author_user_id), "body": comment.body, "created_at": comment.created_at.isoformat()}