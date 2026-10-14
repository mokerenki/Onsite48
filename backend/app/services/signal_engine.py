"""Proactive Signal Engine — evaluates signals and generates recommendations.

Monitors incoming signals, correlates them across sources, and emits action
proposals when signal patterns cross configured thresholds. This is a
pull-based engine: the proactive worker calls `run_cycle()` periodically.
"""
from __future__ import annotations

import structlog
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActionProposal, ProactiveSignal, ProactiveSignalStatus, ProposalStatus
from app.services.proactive_operations import create_proposal, signal_key

logger = structlog.get_logger("onsite48.services.signal_engine")


class SignalPattern:
    """Defines a pattern that triggers an action proposal from signals."""

    def __init__(
        self,
        name: str,
        signal_type: str,
        min_severity: str = "medium",
        min_count: int = 1,
        min_confidence: int = 50,
        time_window_hours: int = 24,
        action_type: str = "auto_diagnose",
        title_template: str = "Signal requires attention",
        rationale_template: str = "Signal of severity {severity} detected for {source}.",
        payload_builder: Optional[Any] = None,
    ):
        self.name = name
        self.signal_type = signal_type
        self.min_severity = min_severity
        self.min_count = min_count
        self.min_confidence = min_confidence
        self.time_window_hours = time_window_hours
        self.action_type = action_type
        self.title_template = title_template
        self.rationale_template = rationale_template
        self.payload_builder = payload_builder


SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}


class ProactiveSignalEngine:
    """Correlates proactive signals into actionable proposals."""

    DEFAULT_PATTERNS: list[SignalPattern] = [
        SignalPattern(
            name="repeated_tool_failure",
            signal_type="tool_failure",
            min_severity="medium",
            min_count=3,
            time_window_hours=72,
            action_type="retry_with_backoff",
            title_template="Repeated tool failures detected",
            rationale_template="Tool {source} has failed {count} times in {window_hours}h. Investigating retry strategy.",
        ),
        SignalPattern(
            name="critical_alert_burst",
            signal_type="error",
            min_severity="high",
            min_count=5,
            time_window_hours=1,
            action_type="escalate",
            title_template="Error burst detected",
            rationale_template="{count} errors of severity {severity} in {window_hours}h.",
        ),
        SignalPattern(
            name="anomaly_spike",
            signal_type="anomaly",
            min_severity="medium",
            min_count=10,
            time_window_hours=24,
            action_type="investigate_anomaly",
            title_template="Anomaly spike detected",
            rationale_template="{count} anomalies detected in {window_hours}h.",
        ),
    ]

    def __init__(self, patterns: list[SignalPattern] | None = None):
        self.patterns = patterns or list(self.DEFAULT_PATTERNS)
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        # Patterns are in-memory; nothing to initialize from disk by default.
        self._initialized = True
        logger.info("signal_engine.initialized", patterns=len(self.patterns))

    async def run_cycle(self, session: AsyncSession) -> dict[str, Any]:
        """Run one signal evaluation cycle. Returns a summary of actions taken."""
        if not self._initialized:
            await self.initialize()

        now = datetime.now(timezone.utc)
        window_start = now - timedelta(hours=1)
        results: list[dict[str, Any]] = []
        proposals_created = 0

        for pattern in self.patterns:
            recent_signals = list((await session.scalars(
                select(ProactiveSignal)
                .where(
                    and_(
                        ProactiveSignal.signal_type == pattern.signal_type,
                        ProactiveSignal.severity.in_(
                            [s for s, v in SEVERITY_ORDER.items() if v >= SEVERITY_ORDER.get(pattern.min_severity, 2)]
                        ),
                        ProactiveSignal.confidence >= pattern.min_confidence,
                        ProactiveSignal.status == ProactiveSignalStatus.OPEN,
                        ProactiveSignal.detected_at >= window_start,
                    )
                ).order_by(ProactiveSignal.detected_at.desc())
            )).all())

            if len(recent_signals) < pattern.min_count:
                continue

            # Check if we already have a pending proposal for this pattern
            existing = await session.scalar(
                select(ActionProposal)
                .where(
                    and_(
                        ActionProposal.action_type == pattern.action_type,
                        ActionProposal.status == ProposalStatus.PENDING,
                    )
                )
            )
            if existing:
                logger.debug("signal_engine.pattern_already_pending", pattern=pattern.name)
                results.append({"pattern": pattern.name, "existing_proposal_id": str(existing.id), "signal_count": len(recent_signals)})
                continue

            # Build the proposal
            sig = recent_signals[0]
            title = pattern.title_template.format(
                source=sig.source,
                severity=sig.severity,
                count=len(recent_signals),
                window_hours=pattern.time_window_hours,
            )
            rationale = pattern.rationale_template.format(
                source=sig.source,
                severity=sig.severity,
                count=len(recent_signals),
                window_hours=pattern.time_window_hours,
            )

            payload: dict[str, Any] = {
                "pattern_name": pattern.name,
                "signal_count": len(recent_signals),
                "signal_ids": [str(s.id) for s in recent_signals[:20]],
                "time_window_hours": pattern.time_window_hours,
            }
            if pattern.payload_builder:
                try:
                    extra = await pattern.payload_builder(session, recent_signals)
                    payload.update(extra)
                except Exception as exc:
                    logger.warning("signal_engine.payload_builder_failed", pattern=pattern.name, error=str(exc)[:120])

            proposal = await create_proposal(
                session,
                organization_id=sig.organization_id,
                user_id=sig.user_id,
                title=title,
                rationale=rationale,
                action_type=pattern.action_type,
                payload=payload,
                connector_id=None,
                signal_id=sig.id,
            )
            proposals_created += 1
            results.append({
                "pattern": pattern.name,
                "proposal_id": str(proposal.id),
                "signal_count": len(recent_signals),
                "title": title,
            })

        logger.info("signal_engine.cycle_complete", proposals_created=proposals_created, results=len(results))
        return {
            "run_at": now.isoformat(),
            "proposals_created": proposals_created,
            "patterns_evaluated": len(self.patterns),
            "details": results,
        }

    async def summarize_signal(
        self,
        session: AsyncSession,
        signal_id: str,
        organization_id: Any,
        user_id: Any,
    ) -> dict[str, Any]:
        """Resolve related proposals and executions for a single signal."""
        signal = await session.get(ProactiveSignal, signal_id)
        if signal is None or signal.organization_id != organization_id or signal.user_id != user_id:
            raise ValueError("Signal not found or not accessible")

        proposals = list((await session.scalars(
            select(ActionProposal)
            .where(ActionProposal.signal_id == signal.id)
            .order_by(ActionProposal.created_at.desc())
        )).all())

        return {
            "id": str(signal.id),
            "source": signal.source,
            "signal_type": signal.signal_type,
            "title": signal.title,
            "summary": signal.summary,
            "severity": signal.severity,
            "confidence": signal.confidence,
            "status": signal.status.value,
            "evidence": signal.evidence,
            "detected_at": signal.detected_at,
            "resolved_at": signal.resolved_at,
            "related_proposals": [
                {
                    "id": str(p.id),
                    "title": p.title,
                    "action_type": p.action_type,
                    "status": p.status.value,
                    "created_at": p.created_at,
                    "decided_at": p.decided_at,
                }
                for p in proposals
            ],
        }


# Module-level singleton
_signal_engine: Optional[ProactiveSignalEngine] = None


def get_signal_engine() -> ProactiveSignalEngine:
    """Get the singleton signal engine instance."""
    global _signal_engine
    if _signal_engine is None:
        _signal_engine = ProactiveSignalEngine()
    return _signal_engine
