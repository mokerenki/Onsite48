"""Phase 5 Proactive Engine API — signal correlation, proposal lifecycle, schedule definitions.

Provides endpoints for running signal engine cycles, listing/dismissing signals,
listing and deciding action proposals, and viewing YAML schedule definitions
alongside their DB-backed schedule rows.
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user, get_db_session
from app.models import (
    ActionProposal,
    OrganizationMember,
    ProactiveSignal,
    ProactiveSignalStatus,
    ProposalStatus,
    ScheduledAgentTask,
    User,
)
from app.services.signal_engine import get_signal_engine

logger = structlog.get_logger("onsite48.api.proactive_engine")

router = APIRouter(prefix="/api/proactive-engine", tags=["proactive engine"])


async def _workspace(session: AsyncSession, user: User) -> OrganizationMember:
    member = await session.scalar(
        select(OrganizationMember)
        .where(OrganizationMember.user_id == user.id)
        .order_by(OrganizationMember.created_at.asc())
    )
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No workspace associated with this account.",
        )
    return member


def _signal_summary(item: ProactiveSignal) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "source": item.source,
        "signal_type": item.signal_type,
        "title": item.title,
        "summary": item.summary,
        "severity": item.severity,
        "confidence": item.confidence,
        "status": item.status.value,
        "evidence": item.evidence,
        "detected_at": item.detected_at.isoformat() if item.detected_at else None,
        "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
    }


def _proposal_summary(item: ActionProposal) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "title": item.title,
        "rationale": item.rationale,
        "action_type": item.action_type,
        "connector_id": item.connector_id,
        "signal_id": str(item.signal_id) if item.signal_id else None,
        "status": item.status.value,
        "payload": item.payload,
        "approved_by_user_id": str(item.approved_by_user_id) if item.approved_by_user_id else None,
        "decided_at": item.decided_at.isoformat() if item.decided_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


@router.post("/signals/run-cycle")
async def run_signal_cycle(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Run one signal engine evaluation cycle and return results."""
    engine = get_signal_engine()
    result = await engine.run_cycle(session)
    return result


@router.get("/signals")
async def list_signals(
    status: str | None = Query(default=None, description="Filter by status: open, dismissed, resolved"),
    severity: str | None = Query(default=None),
    signal_type: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """List proactive signals for the current workspace."""
    member = await _workspace(session, user)

    conditions = [
        ProactiveSignal.organization_id == member.organization_id,
        ProactiveSignal.user_id == user.id,
    ]
    if status:
        try:
            status_enum = ProactiveSignalStatus(status.lower())
            conditions.append(ProactiveSignal.status == status_enum)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {status}",
            )
    if severity:
        conditions.append(ProactiveSignal.severity == severity)
    if signal_type:
        conditions.append(ProactiveSignal.signal_type == signal_type)

    signals = list(
        (
            await session.scalars(
                select(ProactiveSignal)
                .where(and_(*conditions))
                .order_by(ProactiveSignal.detected_at.desc())
                .limit(100)
            )
        ).all()
    )

    return {"signals": [_signal_summary(s) for s in signals], "count": len(signals)}


@router.get("/signals/{signal_id}")
async def get_signal(
    signal_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Get a single signal with related proposals."""
    member = await _workspace(session, user)
    engine = get_signal_engine()

    try:
        result = await engine.summarize_signal(
            session, signal_id, member.organization_id, user.id
        )
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Signal not found.")
    except Exception as exc:
        logger.error("proactive_engine.signal_summary_error", error=str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to summarize signal.")

    return result


@router.post("/signals/{signal_id}/dismiss")
async def dismiss_signal(
    signal_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Mark a signal as dismissed."""
    member = await _workspace(session, user)
    signal = await session.get(ProactiveSignal, signal_id)
    if signal is None or signal.organization_id != member.organization_id or signal.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Signal not found.")
    signal.status = ProactiveSignalStatus.DISMISSED
    signal.resolved_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    await session.commit()
    return {"id": str(signal.id), "status": signal.status.value}


@router.get("/proposals")
async def list_proposals(
    status: str | None = Query(default=None, description="Filter: pending, approved, rejected, executed, failed"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """List action proposals for the current workspace."""
    member = await _workspace(session, user)

    conditions = [
        ActionProposal.organization_id == member.organization_id,
        ActionProposal.user_id == user.id,
    ]
    if status:
        try:
            status_enum = ProposalStatus(status.lower())
            conditions.append(ActionProposal.status == status_enum)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {status}",
            )

    proposals = list(
        (
            await session.scalars(
                select(ActionProposal)
                .where(and_(*conditions))
                .order_by(ActionProposal.created_at.desc())
                .limit(100)
            )
        ).all()
    )

    return {"proposals": [_proposal_summary(p) for p in proposals], "count": len(proposals)}


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Approve a pending proposal."""
    member = await _workspace(session, user)
    proposal = await session.get(ActionProposal, proposal_id)
    if proposal is None or proposal.organization_id != member.organization_id or proposal.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposal not found.")
    if proposal.status != ProposalStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This proposal has already been decided.",
        )
    proposal.status = ProposalStatus.APPROVED
    proposal.approved_by_user_id = user.id
    proposal.decided_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    await session.commit()
    logger.info("proactive_engine.proposal_approved", proposal_id=proposal_id)
    return {"id": str(proposal.id), "status": proposal.status.value}


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Reject a pending proposal."""
    member = await _workspace(session, user)
    proposal = await session.get(ActionProposal, proposal_id)
    if proposal is None or proposal.organization_id != member.organization_id or proposal.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposal not found.")
    if proposal.status != ProposalStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This proposal has already been decided.",
        )
    proposal.status = ProposalStatus.REJECTED
    proposal.approved_by_user_id = user.id
    proposal.decided_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    await session.commit()
    logger.info("proactive_engine.proposal_rejected", proposal_id=proposal_id)
    return {"id": str(proposal.id), "status": proposal.status.value}


@router.get("/scheduled-definitions")
async def list_scheduled_definitions(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """List YAML schedule definitions alongside their DB-backed schedule rows."""
    member = await _workspace(session, user)

    # Load YAML definitions
    yaml_definitions: list[dict[str, Any]] = []
    try:
        from app.services.scheduled_definitions import get_schedule_def_loader

        loader = get_schedule_def_loader()
        loaded = loader.load_definitions()
        defs = loaded.get(str(member.organization_id), loaded.get("default", []))
        for defn in defs:
            yaml_definitions.append({
                "title": defn.title,
                "prompt": defn.prompt,
                "schedule_type": defn.schedule_type,
                "timezone": defn.timezone,
                "local_time": defn.local_time,
                "weekdays": defn.weekdays,
                "month_days": defn.month_days,
                "enabled": defn.enabled,
                "autonomy_policy": defn.autonomy_policy,
                "tags": defn.tags,
                "source": "yaml",
            })
    except Exception as exc:
        logger.warning("proactive_engine.yaml_load_failed", error=str(exc))

    # Load matching DB schedules
    db_schedules = list(
        (
            await session.scalars(
                select(ScheduledAgentTask)
                .where(
                    ScheduledAgentTask.organization_id == member.organization_id,
                    ScheduledAgentTask.user_id == user.id,
                )
                .order_by(ScheduledAgentTask.next_run_at.asc().nullslast(), ScheduledAgentTask.created_at.desc())
                .limit(50)
            )
        ).all()
    )

    db_summary = [
        {
            "id": str(item.id),
            "title": item.title,
            "prompt": item.prompt,
            "schedule_type": item.schedule_type,
            "timezone": item.timezone,
            "run_at": item.run_at.isoformat() if item.run_at else None,
            "next_run_at": item.next_run_at.isoformat() if item.next_run_at else None,
            "last_run_at": item.last_run_at.isoformat() if item.last_run_at else None,
            "enabled": item.enabled,
            "failure_count": item.failure_count,
            "last_error": item.last_error,
            "autonomy_policy": item.autonomy_policy,
            "schedule_policy": item.schedule_policy,
            "source": "database",
        }
        for item in db_schedules
    ]

    return {
        "yaml_definitions": yaml_definitions,
        "db_schedules": db_summary,
    }
