"""Tenant-scoped Heartbeat foundations for business situations and decisions.

Heartbeat observations are structured business facts. They are recorded before a
model is asked to analyse them, making deduplication, auditability, and service
playbook selection deterministic.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProactiveSignal
from app.services.proactive_operations import create_proposal, create_signal

Severity = Literal["low", "medium", "high", "critical"]
Disposition = Literal["observe", "recommend", "delegate"]


@dataclass(frozen=True)
class HeartbeatObservation:
    source: str
    signal_type: str
    title: str
    summary: str
    severity: Severity = "medium"
    confidence: int = 50
    evidence: dict[str, Any] = field(default_factory=dict)
    entity_refs: tuple[str, ...] = ()
    recommended_playbook: str | None = None
    disposition: Disposition = "observe"

    def normalized_evidence(self) -> dict[str, Any]:
        return {
            **self.evidence,
            "heartbeat": {
                "entity_refs": sorted({value.strip() for value in self.entity_refs if value.strip()}),
                "recommended_playbook": self.recommended_playbook,
                "disposition": self.disposition,
            },
        }

    def deduplication_fingerprint(self) -> str:
        payload = {
            "source": self.source.strip().lower(),
            "signal_type": self.signal_type.strip().lower(),
            "title": self.title.strip().lower(),
            "entity_refs": sorted({value.strip().lower() for value in self.entity_refs if value.strip()}),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:64]


class HeartbeatService:
    """Persist business observations and create explicit decisions when needed."""

    async def record_observation(
        self,
        session: AsyncSession,
        *,
        organization_id: Any,
        user_id: Any,
        observation: HeartbeatObservation,
    ) -> ProactiveSignal:
        if not observation.source.strip() or not observation.signal_type.strip() or not observation.title.strip():
            raise ValueError("Heartbeat observations require source, signal type, and title.")
        return await create_signal(
            session,
            organization_id=organization_id,
            user_id=user_id,
            source=observation.source.strip()[:100],
            signal_type=observation.signal_type.strip()[:100],
            title=observation.title.strip()[:300],
            summary=observation.summary.strip()[:8_000],
            severity=observation.severity,
            confidence=max(0, min(100, int(observation.confidence))),
            evidence={
                **observation.normalized_evidence(),
                "deduplication_fingerprint": observation.deduplication_fingerprint(),
            },
            deduplication_key=observation.deduplication_fingerprint(),
        )

    async def create_decision(
        self,
        session: AsyncSession,
        *,
        organization_id: Any,
        user_id: Any,
        signal: ProactiveSignal,
        title: str,
        rationale: str,
        action_type: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """Create an explicit owner decision; it never executes external work."""
        return await create_proposal(
            session,
            organization_id=organization_id,
            user_id=user_id,
            signal_id=signal.id,
            title=title.strip()[:300],
            rationale=rationale.strip()[:8_000],
            action_type=action_type.strip()[:100],
            payload=payload or {},
        )


heartbeat_service = HeartbeatService()