"""Durable, user-scoped proactive operations primitives."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActionProposal, MorningBriefing, ProposalStatus, ProactiveSignal, ProactiveSignalStatus
from app.services.notifications import create_notification


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def signal_key(*, source: str, signal_type: str, title: str) -> str:
    value = f"{source}|{signal_type}|{title.strip().lower()}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:64]


async def create_signal(
    session: AsyncSession,
    *,
    organization_id: Any,
    user_id: Any,
    source: str,
    signal_type: str,
    title: str,
    summary: str,
    severity: str = "medium",
    confidence: int = 50,
    evidence: dict[str, Any] | None = None,
    deduplication_key: str | None = None,
) -> ProactiveSignal:
    key = deduplication_key or signal_key(source=source, signal_type=signal_type, title=title)
    existing = await session.scalar(
        select(ProactiveSignal).where(
            ProactiveSignal.organization_id == organization_id,
            ProactiveSignal.deduplication_key == key,
            ProactiveSignal.status == ProactiveSignalStatus.OPEN,
        )
    )
    if existing:
        existing.summary = summary
        existing.severity = severity
        existing.confidence = confidence
        existing.evidence = evidence or {}
        return existing
    signal = ProactiveSignal(
        organization_id=organization_id,
        user_id=user_id,
        source=source,
        signal_type=signal_type,
        title=title,
        summary=summary,
        severity=severity,
        confidence=max(0, min(100, confidence)),
        deduplication_key=key,
        evidence=evidence or {},
    )
    session.add(signal)
    await session.flush()
    await create_notification(
        session,
        organization_id=organization_id,
        user_id=user_id,
        notification_type="proactive_signal",
        title=f"New {severity} signal: {title}",
        body=summary,
        href="/marketing",
        metadata={"signal_id": str(signal.id), "source": source, "signal_type": signal_type},
    )
    return signal


async def create_proposal(
    session: AsyncSession,
    *,
    organization_id: Any,
    user_id: Any,
    title: str,
    rationale: str,
    action_type: str,
    payload: dict[str, Any],
    connector_id: str | None = None,
    signal_id: Any | None = None,
) -> ActionProposal:
    proposal = ActionProposal(
        organization_id=organization_id,
        user_id=user_id,
        signal_id=signal_id,
        connector_id=connector_id,
        action_type=action_type,
        title=title,
        rationale=rationale,
        payload=payload,
        status=ProposalStatus.PENDING,
    )
    session.add(proposal)
    await session.flush()
    await create_notification(
        session,
        organization_id=organization_id,
        user_id=user_id,
        notification_type="approval_requested",
        title="Approval needed: " + title,
        body=rationale,
        href="/marketing",
        metadata={"proposal_id": str(proposal.id), "action_type": action_type},
    )
    return proposal


async def build_morning_briefing(session: AsyncSession, *, organization_id: Any, user_id: Any) -> MorningBriefing:
    signals = list((await session.scalars(
        select(ProactiveSignal)
        .where(ProactiveSignal.organization_id == organization_id, ProactiveSignal.user_id == user_id, ProactiveSignal.status == ProactiveSignalStatus.OPEN)
        .order_by(ProactiveSignal.severity.desc(), ProactiveSignal.detected_at.desc())
        .limit(20)
    )).all())
    proposals = list((await session.scalars(
        select(ActionProposal)
        .where(ActionProposal.organization_id == organization_id, ActionProposal.user_id == user_id, ActionProposal.status == ProposalStatus.PENDING)
        .order_by(ActionProposal.created_at.desc())
        .limit(20)
    )).all())

    lines = ["# Operating briefing", "", "A current cross-service view of unresolved signals and decisions.", ""]
    if signals:
        lines.extend(["## Signals requiring attention", ""])
        for signal in signals:
            lines.append(f"- **{signal.severity.title()} — {signal.title}**: {signal.summary}")
    else:
        lines.extend(["## Signals requiring attention", "", "No unresolved monitored signals were found."])
    lines.extend(["", "## Approval inbox", ""])
    if proposals:
        for proposal in proposals:
            lines.append(f"- **{proposal.title}** — {proposal.rationale}")
    else:
        lines.append("No actions are awaiting approval.")

    today = date.today().isoformat()
    briefing = await session.scalar(
        select(MorningBriefing).where(
            MorningBriefing.organization_id == organization_id,
            MorningBriefing.user_id == user_id,
            MorningBriefing.briefing_date == today,
        )
    )
    new_briefing = briefing is None
    if briefing is None:
        briefing = MorningBriefing(
            organization_id=organization_id,
            user_id=user_id,
            briefing_date=today,
            summary="\n".join(lines),
            signal_count=len(signals),
            proposal_count=len(proposals),
            metadata_json={"signal_ids": [str(signal.id) for signal in signals], "proposal_ids": [str(proposal.id) for proposal in proposals]},
        )
        session.add(briefing)
    else:
        briefing.summary = "\n".join(lines)
        briefing.signal_count = len(signals)
        briefing.proposal_count = len(proposals)
        briefing.metadata_json = {"signal_ids": [str(signal.id) for signal in signals], "proposal_ids": [str(proposal.id) for proposal in proposals]}
    await session.flush()
    if new_briefing:
        await create_notification(
            session,
            organization_id=organization_id,
            user_id=user_id,
            notification_type="morning_briefing",
            title="Your operating briefing is ready",
            body=f"{len(signals)} open signal(s) and {len(proposals)} decision item(s) need review.",
            href="/command-center",
            metadata={"briefing_id": str(briefing.id)},
        )
    return briefing