"""Transactional outbox claiming and retry helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OutboxEvent


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def claim_batch(session: AsyncSession, *, limit: int = 100, lease_seconds: int = 60) -> list[OutboxEvent]:
    """Claim pending or expired outbox rows using row locks."""
    now = utcnow()
    rows = list((await session.scalars(
        select(OutboxEvent)
        .where(
            or_(
                (OutboxEvent.status == "pending") & (OutboxEvent.available_at <= now),
                (OutboxEvent.status == "processing") & (OutboxEvent.claimed_at < now - timedelta(seconds=lease_seconds)),
            )
        )
        .order_by(OutboxEvent.created_at.asc())
        .limit(max(1, min(limit, 500)))
        .with_for_update(skip_locked=True)
    )).all())
    for row in rows:
        row.status = "processing"
        row.claimed_at = now
        row.attempts += 1
    return rows


def mark_processed(row: OutboxEvent) -> None:
    row.status = "processed"
    row.processed_at = utcnow()
    row.claimed_at = None
    row.last_error = None


def mark_failed(row: OutboxEvent, error: str, *, retry_delay_seconds: int = 30) -> None:
    row.status = "pending"
    row.claimed_at = None
    row.available_at = utcnow() + timedelta(seconds=max(1, retry_delay_seconds))
    row.last_error = str(error)[:2000]


__all__ = ["claim_batch", "mark_failed", "mark_processed"]