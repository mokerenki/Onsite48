"""Task-owned BrowserSession service (Manus-style workspace).

Focused-tab / selected-tab is no longer a valid execution model for new work.
Every session is a durable resource owned by a task. The extension creates a
named tab group and task tabs; the backend owns identity and authorization.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.browser_workspace import (
    TASK_WORKSPACE_MODE,
    BrowserSessionStatus,
    task_workspace_title,
)
from app.models import BrowserAuthorizationRecord, BrowserSession, BrowserSessionEvent, utcnow

AUTHORIZATION_TTL = timedelta(minutes=30)
SESSION_LEASE = timedelta(minutes=5)
TERMINAL = {
    BrowserSessionStatus.STOPPED.value,
    BrowserSessionStatus.FAILED.value,
    BrowserSessionStatus.EXPIRED.value,
    "completed",
    "failed",
    "cancelled",
    "expired",
    "stopped",
}


def summary(item: BrowserSession) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "task_id": str(item.task_id) if item.task_id else None,
        "device_id": str(item.device_id) if item.device_id else None,
        "mode": item.mode,
        "status": item.status,
        "requested_origins": item.requested_origins or [],
        "granted_capabilities": item.granted_capabilities or [],
        "tab_ids": item.tab_ids or [],
        "primary_tab_id": item.primary_tab_id,
        "window_id": getattr(item, "window_id", None),
        "tab_group_id": getattr(item, "tab_group_id", None),
        "tab_group_title": getattr(item, "tab_group_title", None),
        "authorization_id": str(item.authorization_id) if getattr(item, "authorization_id", None) else None,
        "world_revision": getattr(item, "world_revision", 0) or 0,
        "current_step_id": item.current_step_id,
        "current_url": item.current_url,
        "current_observation": item.current_observation or {},
        "lease_expires_at": item.lease_expires_at.isoformat() if item.lease_expires_at else None,
        "authorization_expires_at": item.authorization_expires_at.isoformat() if item.authorization_expires_at else None,
        "stop_reason": item.stop_reason,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


class BrowserSessionService:
    def __init__(self, factory: async_sessionmaker[AsyncSession]):
        self.factory = factory

    async def create(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        task_id: uuid.UUID | None,
        mode: str,
        origins: list[str],
        capabilities: list[str],
        task_name: str | None = None,
    ) -> BrowserSession:
        # Hard reject the old focused-tab model for new sessions.
        if mode in {"focused_tab", "selected_only", "active_tab"}:
            raise ValueError(
                "focused_tab / selected_tab execution model is removed. "
                "Use task_workspace (Manus-style task-owned browser session)."
            )
        safe_mode = TASK_WORKSPACE_MODE
        title = task_workspace_title(task_name or "Task")
        item = BrowserSession(
            organization_id=organization_id,
            user_id=user_id,
            task_id=task_id,
            mode=safe_mode,
            status=BrowserSessionStatus.AUTHORIZING.value,
            requested_origins=sorted(set(origins))[:20],
            granted_capabilities=sorted(set(capabilities))[:20],
            tab_ids=[],
            current_observation={},
            tab_group_title=title,
            world_revision=0,
            authorization_expires_at=utcnow() + AUTHORIZATION_TTL,
        )
        async with self.factory() as session:
            session.add(item)
            await session.flush()
            await self._event(
                session,
                item,
                "browser_session_created",
                {
                    "origins": item.requested_origins,
                    "capabilities": item.granted_capabilities,
                    "mode": safe_mode,
                    "tab_group_title": title,
                },
            )
            await session.commit()
            await session.refresh(item)
            return item

    async def get(
        self, *, session_id: uuid.UUID, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> BrowserSession | None:
        async with self.factory() as session:
            return await session.scalar(
                select(BrowserSession).where(
                    BrowserSession.id == session_id,
                    BrowserSession.organization_id == organization_id,
                    BrowserSession.user_id == user_id,
                )
            )

    async def authorize(
        self,
        *,
        session_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        device_id: uuid.UUID,
        origins: list[str],
        capabilities: list[str],
        tab_ids: list[int] | None = None,
        primary_tab_id: int | None = None,
        window_id: int | None = None,
        tab_group_id: int | None = None,
    ) -> BrowserSession:
        """Server-authoritative authorization. Creates a BrowserAuthorizationRecord."""
        async with self.factory() as session:
            item = await session.scalar(
                select(BrowserSession)
                .where(
                    BrowserSession.id == session_id,
                    BrowserSession.organization_id == organization_id,
                    BrowserSession.user_id == user_id,
                )
                .with_for_update()
            )
            if item is None:
                raise LookupError("Browser session not found.")
            if item.status not in {
                BrowserSessionStatus.AUTHORIZING.value,
                "authorizing",
            }:
                raise ValueError(f"Browser session cannot be authorized from {item.status}.")
            if item.authorization_expires_at and item.authorization_expires_at < utcnow():
                item.status = BrowserSessionStatus.EXPIRED.value
                await session.commit()
                raise ValueError("Browser session authorization expired.")

            auth = BrowserAuthorizationRecord(
                organization_id=organization_id,
                user_id=user_id,
                task_id=item.task_id or uuid.uuid4(),  # should always have task
                device_id=device_id,
                session_id=item.id,
                allowed_origins=sorted(set(origins))[:20],
                allowed_capabilities=sorted(set(capabilities))[:20],
                status="active",
                expires_at=utcnow() + AUTHORIZATION_TTL,
            )
            session.add(auth)
            await session.flush()

            item.device_id = device_id
            item.requested_origins = auth.allowed_origins
            item.granted_capabilities = auth.allowed_capabilities
            item.tab_ids = [int(x) for x in (tab_ids or [])[:20]]
            item.primary_tab_id = primary_tab_id
            item.window_id = window_id
            item.tab_group_id = tab_group_id
            item.authorization_id = auth.id
            item.status = BrowserSessionStatus.AUTHORIZED.value
            item.lease_expires_at = utcnow() + SESSION_LEASE
            item.authorization_expires_at = auth.expires_at

            await self._event(
                session,
                item,
                "browser_session_authorized",
                {
                    "authorization_id": str(auth.id),
                    "origins": item.requested_origins,
                    "capabilities": item.granted_capabilities,
                    "primary_tab_id": primary_tab_id,
                    "window_id": window_id,
                    "tab_group_id": tab_group_id,
                },
            )
            await session.commit()
            await session.refresh(item)
            return item

    async def claim_workspace(
        self,
        *,
        session_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        window_id: int,
        tab_group_id: int | None,
        tab_ids: list[int],
        primary_tab_id: int | None,
    ) -> BrowserSession:
        """Called by the extension after it creates the task tab group + tabs."""
        async with self.factory() as session:
            item = await session.scalar(
                select(BrowserSession)
                .where(
                    BrowserSession.id == session_id,
                    BrowserSession.organization_id == organization_id,
                    BrowserSession.user_id == user_id,
                )
                .with_for_update()
            )
            if item is None:
                raise LookupError("Browser session not found.")
            if item.status not in {
                BrowserSessionStatus.AUTHORIZED.value,
                BrowserSessionStatus.STARTING.value,
                "authorized",
                "starting",
            }:
                raise ValueError(f"Cannot claim workspace from status {item.status}.")
            item.window_id = window_id
            item.tab_group_id = tab_group_id
            item.tab_ids = [int(x) for x in tab_ids[:20]]
            item.primary_tab_id = primary_tab_id or (item.tab_ids[0] if item.tab_ids else None)
            item.status = BrowserSessionStatus.ACTIVE.value
            item.lease_expires_at = utcnow() + SESSION_LEASE
            await self._event(
                session,
                item,
                "browser_workspace_claimed",
                {
                    "window_id": window_id,
                    "tab_group_id": tab_group_id,
                    "tab_ids": item.tab_ids,
                    "primary_tab_id": item.primary_tab_id,
                },
            )
            await session.commit()
            await session.refresh(item)
            return item

    async def transition(
        self,
        *,
        session_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        status: str,
        reason: str | None = None,
    ) -> BrowserSession:
        async with self.factory() as session:
            item = await session.scalar(
                select(BrowserSession)
                .where(
                    BrowserSession.id == session_id,
                    BrowserSession.organization_id == organization_id,
                    BrowserSession.user_id == user_id,
                )
                .with_for_update()
            )
            if item is None:
                raise LookupError("Browser session not found.")
            if item.status in TERMINAL and status not in TERMINAL:
                raise ValueError("Browser session is terminal.")
            # Normalize legacy "running"/"resumed" to ACTIVE / RESUMING
            normalized = status
            if status in {"running", "resumed", "active"}:
                normalized = BrowserSessionStatus.ACTIVE.value
            elif status == "takeover":
                normalized = BrowserSessionStatus.TAKEN_OVER.value
            elif status == "paused":
                normalized = BrowserSessionStatus.PAUSED_FOR_USER.value
            item.status = normalized
            item.stop_reason = reason if normalized in TERMINAL else item.stop_reason
            item.lease_expires_at = None if normalized in TERMINAL else utcnow() + SESSION_LEASE
            await self._event(
                session,
                item,
                f"browser_session_{normalized}",
                {"reason": reason, "previous": item.status},
            )
            await session.commit()
            await session.refresh(item)
            return item

    async def stop(
        self,
        *,
        session_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        reason: str = "user_stop",
    ) -> BrowserSession:
        """Revoke lease and mark session stopped. Queued commands must be rejected."""
        async with self.factory() as session:
            item = await session.scalar(
                select(BrowserSession)
                .where(
                    BrowserSession.id == session_id,
                    BrowserSession.organization_id == organization_id,
                    BrowserSession.user_id == user_id,
                )
                .with_for_update()
            )
            if item is None:
                raise LookupError("Browser session not found.")
            item.status = BrowserSessionStatus.STOPPED.value
            item.stop_reason = reason[:1000]
            item.lease_expires_at = None
            # Revoke authorization
            if item.authorization_id:
                auth = await session.scalar(
                    select(BrowserAuthorizationRecord).where(
                        BrowserAuthorizationRecord.id == item.authorization_id
                    )
                )
                if auth and auth.status == "active":
                    auth.status = "revoked"
                    auth.revoked_at = utcnow()
            await self._event(
                session,
                item,
                "BROWSER_SESSION_STOPPED",
                {"reason": reason},
            )
            await session.commit()
            await session.refresh(item)
            return item

    async def record_observation(
        self,
        *,
        session_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        snapshot: dict[str, Any],
        event_type: str = "observation",
        diff: dict[str, Any] | None = None,
        challenge: dict[str, Any] | None = None,
    ) -> BrowserSession:
        async with self.factory() as session:
            item = await session.scalar(
                select(BrowserSession)
                .where(
                    BrowserSession.id == session_id,
                    BrowserSession.organization_id == organization_id,
                    BrowserSession.user_id == user_id,
                )
                .with_for_update()
            )
            if item is None:
                raise LookupError("Browser session not found.")
            bounded = {k: snapshot.get(k) for k in snapshot if k in (
                "url", "title", "origin", "tab_id", "window_id", "viewport",
                "loading_state", "navigation_state", "scroll", "element_count",
                "page_digest", "observed_at", "observation_id",
            )}
            observation = {
                "snapshot": bounded,
                "diff": diff or {},
                "challenge": challenge,
            }
            item.current_url = str(snapshot.get("url") or item.current_url or "")[:2048] or None
            tab_id = bounded.get("tab_id")
            if isinstance(tab_id, int):
                if bounded.get("event") == "closed":
                    item.tab_ids = [v for v in (item.tab_ids or []) if v != tab_id]
                    if item.primary_tab_id == tab_id:
                        item.primary_tab_id = (item.tab_ids or [None])[0]
                elif tab_id not in (item.tab_ids or []):
                    item.tab_ids = [*list(item.tab_ids or []), tab_id][:20]
                if item.primary_tab_id is None:
                    item.primary_tab_id = tab_id
            if isinstance(bounded.get("window_id"), int):
                item.window_id = bounded["window_id"]
            item.current_observation = observation
            item.world_revision = (item.world_revision or 0) + 1
            if challenge and challenge.get("detected") and item.status == BrowserSessionStatus.ACTIVE.value:
                item.status = BrowserSessionStatus.TAKEN_OVER.value
                item.stop_reason = str(challenge.get("reason") or "Manual browser verification is required.")[:1000]
            item.lease_expires_at = utcnow() + SESSION_LEASE
            await self._event(session, item, event_type, observation)
            await session.commit()
            await session.refresh(item)
            return item

    async def events(
        self, *, session_id: uuid.UUID, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[BrowserSessionEvent]:
        async with self.factory() as session:
            return list(
                (
                    await session.scalars(
                        select(BrowserSessionEvent)
                        .join(BrowserSession)
                        .where(
                            BrowserSessionEvent.session_id == session_id,
                            BrowserSession.organization_id == organization_id,
                            BrowserSession.user_id == user_id,
                        )
                        .order_by(BrowserSessionEvent.sequence)
                    )
                ).all()
            )

    async def _event(
        self, session: AsyncSession, item: BrowserSession, event_type: str, payload: dict[str, Any]
    ) -> None:
        latest = await session.scalar(
            select(func.max(BrowserSessionEvent.sequence)).where(
                BrowserSessionEvent.session_id == item.id
            )
        )
        session.add(
            BrowserSessionEvent(
                session_id=item.id,
                task_id=item.task_id,
                sequence=int(latest or 0) + 1,
                event_type=event_type,
                payload=payload,
            )
        )
