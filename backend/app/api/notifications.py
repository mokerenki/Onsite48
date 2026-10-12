"""Authenticated in-app notification inbox API."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.files import workspace_membership
from app.core.security import get_current_user, get_db_session
from app.models import Notification, User

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def serialize(item: Notification) -> dict[str, Any]:
    return {
        "id": str(item.id), "type": item.notification_type, "title": item.title, "body": item.body,
        "href": item.href, "metadata": item.metadata_json or {}, "read_at": item.read_at.isoformat() if item.read_at else None,
        "created_at": item.created_at.isoformat(),
    }


@router.get("")
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(default=30, ge=1, le=100),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    membership = await workspace_membership(session, user)
    query = select(Notification).where(Notification.organization_id == membership.organization_id, Notification.user_id == user.id)
    if unread_only: query = query.where(Notification.read_at.is_(None))
    rows = list((await session.scalars(query.order_by(Notification.created_at.desc()).limit(limit))).all())
    return {"notifications": [serialize(item) for item in rows], "unread_count": sum(1 for item in rows if item.read_at is None)}


@router.post("/{notification_id}/read")
async def mark_read(
    notification_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    membership = await workspace_membership(session, user)
    item = await session.scalar(select(Notification).where(Notification.id == notification_id, Notification.organization_id == membership.organization_id, Notification.user_id == user.id))
    if item is None: raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found.")
    item.read_at = item.read_at or datetime.now(timezone.utc)
    await session.commit()
    return serialize(item)


@router.post("/read-all")
async def mark_all_read(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)):
    membership = await workspace_membership(session, user)
    rows = list((await session.scalars(select(Notification).where(Notification.organization_id == membership.organization_id, Notification.user_id == user.id, Notification.read_at.is_(None)))).all())
    now = datetime.now(timezone.utc)
    for item in rows: item.read_at = now
    await session.commit()
    return {"updated": len(rows)}