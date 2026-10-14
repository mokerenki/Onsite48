"""Shared policy types to avoid circular imports between policy.py and policy_engine.py."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Decision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    APPROVAL = "APPROVAL"
    TAKEOVER = "TAKEOVER"


@dataclass
class PolicyDecisionResult:
    decision: Decision
    reason: str
    core: Optional[Any] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "metadata": self.metadata,
        }