"""Tenant-scoped workspace roles and authorization primitives."""
from __future__ import annotations
from typing import Any
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import get_current_user, get_db_session
from app.models import OrganizationMember, User, UserRole
from app.api.product_tasks import get_current_membership

router = APIRouter(prefix="/api/rbac", tags=["rbac"])
ROLE_PERMISSIONS: dict[UserRole, set[str]] = {
    UserRole.OWNER: {"workspace:read", "workspace:manage", "members:manage", "tasks:manage"},
    UserRole.ADMIN: {"workspace:read", "members:manage", "tasks:manage"},
    UserRole.MEMBER: {"workspace:read", "tasks:manage"},
}

class RoleUpdate(BaseModel):
    role: UserRole

def _member_payload(member: OrganizationMember) -> dict[str, Any]:
    return {"user_id": str(member.user_id), "organization_id": str(member.organization_id), "role": member.role.value, "created_at": member.created_at.isoformat()}

async def require_member_admin(session: AsyncSession, user: User) -> OrganizationMember:
    membership = await get_current_membership(session, user)
    if membership.role not in {UserRole.OWNER, UserRole.ADMIN}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workspace administrator permission required")
    return membership

@router.get("/me")
async def current_workspace_role(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    membership = await get_current_membership(session, user)
    return {**_member_payload(membership), "permissions": sorted(ROLE_PERMISSIONS[membership.role])}

@router.get("/members")
async def list_members(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> list[dict[str, Any]]:
    membership = await get_current_membership(session, user)
    rows = (await session.scalars(select(OrganizationMember).where(OrganizationMember.organization_id == membership.organization_id).order_by(OrganizationMember.created_at.asc()))).all()
    return [_member_payload(row) for row in rows]

@router.patch("/members/{user_id}/role")
async def update_member_role(user_id: UUID, payload: RoleUpdate, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    actor = await require_member_admin(session, user)
    target = await session.scalar(select(OrganizationMember).where(OrganizationMember.organization_id == actor.organization_id, OrganizationMember.user_id == user_id))
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found in this workspace")
    if target.role == UserRole.OWNER and payload.role != UserRole.OWNER:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Owner transfer requires the ownership workflow")
    if actor.role != UserRole.OWNER and payload.role == UserRole.OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the workspace owner can assign ownership")
    target.role = payload.role
    await session.commit()
    await session.refresh(target)
    return _member_payload(target)

@router.get("/roles")
async def list_roles(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> list[dict[str, Any]]:
    await get_current_membership(session, user)
    return [{"role": role.value, "permissions": sorted(permissions)} for role, permissions in ROLE_PERMISSIONS.items()]