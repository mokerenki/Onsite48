"""Capability-aware model lanes for the agent runtime.

Lanes describe *work*, not vendors. A deployment can point each lane at any
OpenAI-compatible provider while the planner and workers remain unchanged.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

class ModelLane(StrEnum):
    FAST = "fast"
    PLANNER = "planner"
    CODER = "coder"
    RESEARCHER = "researcher"
    BROWSER = "browser"
    DOCUMENT = "document"
    SPREADSHEET = "spreadsheet"
    VERIFIER = "verifier"

@dataclass(frozen=True)
class LaneDescriptor:
    lane: ModelLane
    purpose: str
    required_capabilities: tuple[str, ...]
    env_provider: str
    env_model: str
    default_role: str
    budget_class: str

LANE_DESCRIPTORS: tuple[LaneDescriptor, ...] = (
    LaneDescriptor(ModelLane.FAST, "classification, titles, metadata, and short follow-ups", ("chat", "structured"), "SYNTHAI_FAST_PROVIDER", "FAST_MODEL", "fallback", "economy"),
    LaneDescriptor(ModelLane.PLANNER, "bounded plans and intent decomposition", ("chat", "structured", "tools"), "SYNTHAI_PLANNING_PROVIDER", "PLANNER_MODEL", "planning", "balanced"),
    LaneDescriptor(ModelLane.CODER, "repository edits, debugging, and code review", ("chat", "structured", "tools", "long_context"), "SYNTHAI_CODER_PROVIDER", "CODER_MODEL", "planning", "complex"),
    LaneDescriptor(ModelLane.RESEARCHER, "source-grounded research and synthesis", ("chat", "structured", "tools", "long_context"), "SYNTHAI_RESEARCH_PROVIDER", "RESEARCH_MODEL", "planning", "balanced"),
    LaneDescriptor(ModelLane.BROWSER, "browser instructions, state interpretation, and recovery", ("chat", "structured", "tools", "vision"), "SYNTHAI_BROWSER_PROVIDER", "BROWSER_MODEL", "verification", "complex"),
    LaneDescriptor(ModelLane.DOCUMENT, "document structure, editing, and export planning", ("chat", "structured", "long_context"), "SYNTHAI_DOCUMENT_PROVIDER", "DOCUMENT_MODEL", "planning", "balanced"),
    LaneDescriptor(ModelLane.SPREADSHEET, "tabular reasoning, formulas, and validation", ("chat", "structured", "tools"), "SYNTHAI_SPREADSHEET_PROVIDER", "SPREADSHEET_MODEL", "planning", "complex"),
    LaneDescriptor(ModelLane.VERIFIER, "postcondition checks, critique, and safe completion", ("chat", "structured"), "SYNTHAI_VERIFICATION_PROVIDER", "VERIFIER_MODEL", "verification", "balanced"),
)

def lane_catalog() -> list[dict[str, Any]]:
    return [asdict(item) | {"lane": item.lane.value} for item in LANE_DESCRIPTORS]

def lane_for_intent(*, domain: str | None = None, prompt: str = "") -> ModelLane:
    text = f"{domain or ''} {prompt}".lower()
    if any(word in text for word in ("spreadsheet", "excel", "csv", "formula", "pivot")):
        return ModelLane.SPREADSHEET
    if any(word in text for word in ("document", "report", "memo", "proposal", "write")):
        return ModelLane.DOCUMENT
    if any(word in text for word in ("code", "repository", "bug", "api", "refactor", "test")):
        return ModelLane.CODER
    if any(word in text for word in ("browser", "website", "click", "navigate", "tab")):
        return ModelLane.BROWSER
    if any(word in text for word in ("research", "compare", "sources", "investigate")):
        return ModelLane.RESEARCHER
    return ModelLane.PLANNER