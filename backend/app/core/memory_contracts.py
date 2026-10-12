"""Typed contracts for working, episodic, user, and project memory.

Memory is explicit and provenance-bearing. A model suggestion is not a durable
memory write until it has a source, confidence, and an accepted scope.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class MemoryLayer(StrEnum):
    WORKING = "working"
    EPISODIC = "episodic"
    USER_PREFERENCE = "user_preference"
    PROJECT = "project"


class MemoryConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=240)
    value: Any
    layer: MemoryLayer
    confidence: MemoryConfidence = MemoryConfidence.MEDIUM
    source: str = Field(min_length=1, max_length=500)
    task_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=32)
    user_approved: bool = False
    expires_at: str | None = None


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=128)
    source_type: str = Field(min_length=1, max_length=64)
    source_uri: str | None = None
    observation_id: str | None = None
    tool_invocation_id: str | None = None
    extracted_value: Any = None
    verified: bool = False
    captured_at: str | None = None


class GroundedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    evidence: list[EvidenceRef] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
    side_effects: list[str] = Field(default_factory=list)
    completion_status: str = "verified"

    @property
    def is_grounded(self) -> bool:
        return bool(self.evidence) and all(item.verified for item in self.evidence)


def working_memory(*, goal: str, constraints: list[str] | None = None, open_questions: list[str] | None = None) -> dict[str, Any]:
    return {"goal": goal.strip(), "constraints": constraints or [], "open_questions": open_questions or [], "active_entities": [], "checkpoint": "requirements_understood"}


def accept_memory(record: MemoryRecord, *, allow_unapproved_preference: bool = False) -> bool:
    if record.layer in {MemoryLayer.USER_PREFERENCE, MemoryLayer.PROJECT} and not record.user_approved and not allow_unapproved_preference:
        return False
    if record.confidence == MemoryConfidence.LOW and not record.evidence_ids:
        return False
    return True


def grounding_report(answer: str, evidence: list[EvidenceRef]) -> dict[str, Any]:
    # Runtime events are JSON dictionaries while typed callers may pass
    # EvidenceRef instances. Normalize both forms at this boundary.
    def is_verified(item: EvidenceRef | dict[str, Any]) -> bool:
        return bool(item.verified if isinstance(item, EvidenceRef) else item.get("verified"))

    def evidence_id(item: EvidenceRef | dict[str, Any]) -> str:
        return str(item.evidence_id if isinstance(item, EvidenceRef) else item.get("evidence_id") or "")

    verified = [item for item in evidence if is_verified(item)]
    return {
        "grounded": bool(verified),
        "evidence_ids": [evidence_id(item) for item in verified if evidence_id(item)],
        "unverified_evidence_ids": [evidence_id(item) for item in evidence if not is_verified(item) and evidence_id(item)],
        "claimable_fact_count": len(verified),
        "warning": None if verified else "No verified evidence is attached to this answer.",
        "answer_length": len(answer),
    }


def memory_write_decision(event: dict[str, Any]) -> tuple[MemoryRecord | None, str | None]:
    """Turn a raw memory event into an accepted record, or a reason to refuse.

    The typed contracts in this module existed but nothing in the runtime used
    them: every event went into one undifferentiated store, so a user
    preference, an episodic observation and a project fact were indistinguishable
    at retrieval, and no record could ever expire. This is the enforcement
    point, and it is deliberately a pure function so it can be tested without
    Cognee, Qdrant or Kuzu.

    Refusing is the point. Storing an unapproved preference as though the user
    had stated it is what makes an assistant untrustworthy, and it is silent, so
    the reason is returned for the caller to log.
    """
    if not isinstance(event, dict):
        return None, "event is not a mapping"

    content = str(event.get("content") or "").strip()
    if not content:
        return None, "empty content"

    raw_layer = str(event.get("layer") or "").strip().lower()
    if not raw_layer:
        # An event log is the least durable claim, so it is the safe default.
        raw_layer = MemoryLayer.EPISODIC.value
    try:
        layer = MemoryLayer(raw_layer)
    except ValueError:
        return None, f"unknown memory layer {raw_layer!r}"

    source = str(event.get("source") or "").strip() or "unspecified"

    raw_confidence = str(event.get("confidence") or "").strip().lower()
    try:
        confidence = (
            MemoryConfidence(raw_confidence) if raw_confidence else MemoryConfidence.MEDIUM
        )
    except ValueError:
        return None, f"unknown memory confidence {raw_confidence!r}"

    expires_at = event.get("expires_at")
    if expires_at is not None:
        expires_at = str(expires_at).strip() or None

    evidence_ids = event.get("evidence_ids") or []
    if not isinstance(evidence_ids, list):
        return None, "evidence_ids must be a list"

    try:
        record = MemoryRecord(
            key=str(event.get("event_id") or event.get("key") or source)[:240],
            value=content,
            layer=layer,
            confidence=confidence,
            source=source[:500],
            task_id=event.get("task_id"),
            evidence_ids=[str(item) for item in evidence_ids][:32],
            user_approved=bool(event.get("user_approved", False)),
            expires_at=expires_at,
        )
    except ValidationError as error:
        return None, f"invalid memory record: {error.error_count()} field error(s)"

    allow_unapproved = bool(event.get("allow_unapproved_preference", False))
    if not accept_memory(record, allow_unapproved_preference=allow_unapproved):
        if record.layer in {MemoryLayer.USER_PREFERENCE, MemoryLayer.PROJECT} and not record.user_approved:
            return None, (
                f"{record.layer.value} memory requires user approval; refusing to store "
                "an unapproved preference or project fact as durable truth"
            )
        return None, "low-confidence memory requires supporting evidence"

    return record, None


def expiry_epoch(expires_at: str | None) -> float | None:
    """Epoch seconds for an expiry, or None when absent or unreadable.

    Stored alongside the ISO string because Qdrant's range filter is numeric
    only; a range over the ISO text is rejected by the client at construction.
    An unreadable timestamp yields None, which means 'does not expire'.
    """
    if not expires_at:
        return None
    try:
        deadline = datetime.fromisoformat(str(expires_at))
    except (TypeError, ValueError):
        return None
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline.timestamp()


def is_expired(record: MemoryRecord | dict[str, Any], *, now: datetime | None = None) -> bool:
    """Whether a stored memory record is past its expiry.

    A missing or unparseable expiry means 'does not expire'. That is the only
    safe reading of an unreadable timestamp: silently treating it as expired
    would delete real facts.
    """
    expires_at = record.expires_at if isinstance(record, MemoryRecord) else record.get("expires_at")
    if not expires_at:
        return False
    moment = now or datetime.now(timezone.utc)
    try:
        deadline = datetime.fromisoformat(str(expires_at))
    except (TypeError, ValueError):
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline <= moment


__all__ = [
    "EvidenceRef",
    "GroundedAnswer",
    "MemoryConfidence",
    "MemoryLayer",
    "MemoryRecord",
    "accept_memory",
    "expiry_epoch",
    "grounding_report",
    "is_expired",
    "memory_write_decision",
    "working_memory",
]