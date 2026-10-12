"""Durable in-app notifications scoped to a single organization member."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification


async def create_notification(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    notification_type: str,
    title: str,
    body: str,
    href: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Notification:
    notification = Notification(
        organization_id=organization_id,
        user_id=user_id,
        notification_type=notification_type[:64],
        title=title[:300],
        body=body[:8000],
        href=(href or "")[:512] or None,
        metadata_json=metadata or {},
    )
    session.add(notification)
    await session.flush()
    return notification