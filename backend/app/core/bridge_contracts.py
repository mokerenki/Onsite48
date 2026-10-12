"""Structured Onsite48 Bridge observation and action verification contracts."""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class InteractiveElement(BaseModel):
    model_config = ConfigDict(extra="allow")

    element_id: str = Field(min_length=1, max_length=128)
    role: str = "unknown"
    name: str = ""
    bounds: dict[str, float] = Field(default_factory=dict)
    enabled: bool = True
    selector: str | None = None


class BridgeObservation(BaseModel):
    model_config = ConfigDict(extra="allow")

    observation_id: str = Field(min_length=1, max_length=128)
    tab_id: int
    window_id: int | None = None
    url: str = ""
    title: str = ""
    visible_text: str = ""
    elements: list[InteractiveElement] = Field(default_factory=list)
    captured_at: str | None = None
    page_digest: str | None = None


class BridgeActionExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    expected_effect: str = Field(min_length=3)
    verification_rules: list[dict[str, Any]] = Field(default_factory=list)
    max_recovery_attempts: int = Field(default=2, ge=0, le=3)


class BridgeActionReceipt(BaseModel):
    model_config = ConfigDict(extra="allow")

    command_id: str
    observation_before: BridgeObservation | None = None
    observation_after: BridgeObservation | None = None
    verification: dict[str, Any] = Field(default_factory=dict)
    status: str
    recovery_attempts: int = 0

    @property
    def verified(self) -> bool:
        return self.status == "verified" and bool(self.verification.get("passed", 0))


def compare_observations(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    if not before or not after:
        return {"status": "inconclusive", "reason": "Comparable before/after observations were unavailable."}
    before_page = before.get("observation", before).get("page", {})
    after_page = after.get("observation", after).get("page", {})
    return {
        "status": "changed" if before_page != after_page else "unchanged",
        "tab_continuity": before.get("tab_id") == after.get("tab_id"),
        "url_before": before_page.get("url"),
        "url_after": after_page.get("url"),
        "digest_before": before_page.get("page_digest"),
        "digest_after": after_page.get("page_digest"),
    }