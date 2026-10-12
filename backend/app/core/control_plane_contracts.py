"""Authoritative control-plane contracts for intent, capabilities, results, and validation."""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class IntentOperation(StrEnum):
    COUNT = "count"
    LIST = "list"
    GET = "get"
    SEARCH = "search"
    READ = "read"
    SUMMARIZE = "summarize"
    COMPARE = "compare"
    CREATE = "create"
    UPDATE = "update"
    SEND = "send"
    DELETE = "delete"
    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    VERIFY = "verify"
    EXECUTE = "execute"


class IntentContract(BaseModel):
    """The meaning of a request before a tool or plan is selected."""

    model_config = ConfigDict(extra="forbid")

    domain: str = Field(min_length=1, max_length=64)
    provider: str | None = Field(default=None, max_length=128)
    resource: str = Field(min_length=1, max_length=160)
    operation: IntentOperation
    filters: dict[str, Any] = Field(default_factory=dict)
    entities: dict[str, Any] = Field(default_factory=dict)
    requested_quantity: int | None = Field(default=None, ge=1)
    exactness: Literal["exact", "estimated", "paginated", "not_applicable"] = "not_applicable"
    output_style: Literal["scalar", "list", "record", "summary", "artifact", "action"] = "summary"
    mutates_external_state: bool = False
    requires_approval: bool = False
    requires_user_takeover: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    parser_version: str = "intent-v1"
    clarification_question: str | None = None


class CapabilityContract(BaseModel):
    """What a capability can and cannot do."""

    model_config = ConfigDict(extra="forbid")

    name: str
    domain: str
    provider: str | None = None
    # ``resources`` is retained for compatibility with existing tool schemas;
    # ``resource`` is the canonical field for single-resource capabilities.
    resource: str | None = None
    resources: list[str] = Field(default_factory=list)
    operations: list[IntentOperation] = Field(default_factory=list)
    excludes_operations: list[IntentOperation] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    evidence_schema: dict[str, Any] = Field(default_factory=dict)
    read_only: bool = True
    reversible: bool = True
    irreversible: bool = False
    exactness: Literal["exact", "estimated", "paginated", "unknown"] = "unknown"
    supports_pagination: bool = False
    required_scopes: list[str] = Field(default_factory=list)
    idempotency: str = "read_only"
    version: str = "capability-v1"


class PaginationEnvelope(BaseModel):
    has_more: bool = False
    next_page_token: str | None = None
    total_count: int | None = None


class ResultEnvelope(BaseModel):
    """Authoritative tool result. Human-readable output is only a rendering."""

    model_config = ConfigDict(extra="allow")

    resource: str
    operation: IntentOperation
    status: Literal["success", "empty", "partial", "failed", "blocked"]
    exact: bool | None = None
    data: Any = None
    pagination: PaginationEnvelope = Field(default_factory=PaginationEnvelope)
    evidence: dict[str, Any] = Field(default_factory=dict)
    provider_request_id: str | None = None
    warnings: list[str] = Field(default_factory=list)
    freshness: str | None = None


def capability_matches_intent(intent: IntentContract, capability: CapabilityContract) -> tuple[bool, list[str]]:
    """Reject semantic mismatches before execution."""
    errors: list[str] = []
    aliases = {
        "email_drafts": "gmail_draft",
        "email_messages": "gmail_message",
        # Open-domain / planner fallbacks must be able to use public web tools.
        "general_resource": "web_page",
        "research_report": "web_page",
    }
    intent_resource = aliases.get(intent.resource, intent.resource)
    capability_resources = [aliases.get(item, item) for item in capability.resources]
    if intent.domain != capability.domain and capability.domain not in {"general", "multi"}:
        errors.append(f"domain mismatch: intent={intent.domain}, capability={capability.domain}")
    if intent.provider and capability.provider and intent.provider != capability.provider:
        errors.append(f"provider mismatch: intent={intent.provider}, capability={capability.provider}")
    if intent_resource not in capability_resources:
        errors.append(f"resource '{intent.resource}' is not supported by {capability.name}")
    if intent.operation in capability.excludes_operations:
        errors.append(f"operation '{intent.operation}' is explicitly excluded by {capability.name}")
    if capability.operations and intent.operation not in capability.operations:
        errors.append(f"operation '{intent.operation}' is not supported by {capability.name}")
    if intent.operation == IntentOperation.COUNT and capability.exactness == "paginated":
        errors.append(f"paginated capability {capability.name} cannot satisfy an exact count")
    if intent.mutates_external_state and capability.read_only:
        errors.append(f"read-only capability {capability.name} cannot satisfy a mutation")
    return not errors, errors


def ensure_result_envelope(value: Any, *, intent: IntentContract) -> ResultEnvelope:
    """Normalize legacy tool output without losing the requested semantics."""
    if isinstance(value, ResultEnvelope):
        return value
    if isinstance(value, dict) and {"resource", "operation", "status"}.issubset(value):
        return ResultEnvelope.model_validate(value)
    return ResultEnvelope(
        resource=intent.resource,
        operation=intent.operation,
        status="success" if value is not None else "empty",
        exact=intent.exactness == "exact",
        data=value,
        warnings=["Legacy tool output was normalized; provider envelope metadata was unavailable."],
    )