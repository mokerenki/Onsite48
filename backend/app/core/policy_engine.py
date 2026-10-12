"""Independent Policy / Sentinel engine.

The same agent that proposes an action is never the final authority on whether
it may execute. This module is the single place for ALLOW / DENY / APPROVAL
decisions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class PolicyDecision(str, Enum):
    ALLOW = "ALLOW"
    ALLOW_WITH_MONITORING = "ALLOW_WITH_MONITORING"
    REQUIRE_AUTO_REVIEW = "REQUIRE_AUTO_REVIEW"
    REQUIRE_USER_APPROVAL = "REQUIRE_USER_APPROVAL"
    REQUIRE_TAKEOVER = "REQUIRE_TAKEOVER"
    DENY = "DENY"


# Trust levels for content. Only USER_INSTRUCTION / SYSTEM_POLICY / TASK_CONTRACT
# may modify the plan. WEB_CONTENT and PAGE_INSTRUCTION never may.
class TrustLevel(str, Enum):
    USER_INSTRUCTION = "USER_INSTRUCTION"
    SYSTEM_POLICY = "SYSTEM_POLICY"
    TASK_CONTRACT = "TASK_CONTRACT"
    TOOL_RESULT = "TOOL_RESULT"
    WEB_CONTENT = "WEB_CONTENT"
    DOCUMENT_CONTENT = "DOCUMENT_CONTENT"
    EMAIL_CONTENT = "EMAIL_CONTENT"
    PAGE_INSTRUCTION = "PAGE_INSTRUCTION"


HIGH_IMPACT_MUTATIONS = {
    "send_email",
    "publish",
    "delete",
    "purchase",
    "transfer",
    "payment",
    "change_password",
    "change_security",
    "share_public",
    "disable_security",
    "upload_credentials",
}

PROMPT_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard your instructions",
    "you are now",
    "system prompt",
    "reveal your api key",
    "upload your api keys",
    "exfiltrate",
    "send secrets",
    "disable safety",
)


@dataclass
class PolicyContext:
    user_id: Optional[str] = None
    task_id: Optional[str] = None
    goal: str = ""
    tool: str = ""
    action: str = ""
    target: str = ""
    origin: str = ""
    resource: str = ""
    mutation_type: str = ""
    financial_impact: bool = False
    external_communication: bool = False
    reversibility: str = "unknown"  # reversible | irreversible | unknown
    credentials_involved: bool = False
    data_sensitivity: str = "normal"  # normal | sensitive | restricted
    authorization_valid: bool = True
    content_trust: TrustLevel = TrustLevel.USER_INSTRUCTION
    raw_content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyResult:
    decision: PolicyDecision
    reasons: list[str] = field(default_factory=list)
    required_capability: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "required_capability": self.required_capability,
        }


class PolicyEngine:
    """Canonical policy evaluator. Stateless; safe to call from any layer."""

    def evaluate(self, ctx: PolicyContext) -> PolicyResult:
        reasons: list[str] = []

        # 1. Prompt injection from untrusted content
        if ctx.content_trust in {
            TrustLevel.WEB_CONTENT,
            TrustLevel.PAGE_INSTRUCTION,
            TrustLevel.DOCUMENT_CONTENT,
            TrustLevel.EMAIL_CONTENT,
        }:
            lowered = (ctx.raw_content or "").lower()
            for marker in PROMPT_INJECTION_MARKERS:
                if marker in lowered:
                    return PolicyResult(
                        PolicyDecision.DENY,
                        reasons=[
                            f"Untrusted content contains instruction-like language ({marker!r}). "
                            "Page/document content cannot modify the plan."
                        ],
                    )

        # 2. Authorization must be valid for browser / tool mutations
        if not ctx.authorization_valid and ctx.mutation_type:
            return PolicyResult(
                PolicyDecision.REQUIRE_TAKEOVER,
                reasons=["No valid server-side authorization for this action."],
            )

        # 3. Forbidden high-impact without approval path
        mutation = (ctx.mutation_type or ctx.action or "").lower().strip()
        if mutation in HIGH_IMPACT_MUTATIONS or ctx.financial_impact:
            reasons.append(f"High-impact mutation: {mutation or 'financial'}")
            return PolicyResult(PolicyDecision.REQUIRE_USER_APPROVAL, reasons=reasons)

        if ctx.external_communication and ctx.reversibility == "irreversible":
            reasons.append("Irreversible external communication")
            return PolicyResult(PolicyDecision.REQUIRE_USER_APPROVAL, reasons=reasons)

        if ctx.credentials_involved:
            return PolicyResult(
                PolicyDecision.REQUIRE_TAKEOVER,
                reasons=["Credential or security-sensitive interaction requires user control."],
            )

        if ctx.data_sensitivity == "restricted":
            return PolicyResult(
                PolicyDecision.REQUIRE_USER_APPROVAL,
                reasons=["Restricted data sensitivity"],
            )

        # 4. Reads and reversible local actions
        if (ctx.mutation_type or "").lower() in {"", "read", "observe", "navigate", "search"}:
            return PolicyResult(PolicyDecision.ALLOW, reasons=["Read/observe path"])

        # 5. Default: monitor writes that are not high-impact
        if (ctx.mutation_type or "").lower() in {"create_draft", "update_draft", "fill", "type", "click"}:
            return PolicyResult(
                PolicyDecision.ALLOW_WITH_MONITORING,
                reasons=["Bounded write under monitoring"],
            )

        # 6. Workspace file writes (file_write / edit) — not external high-impact
        if (ctx.mutation_type or "").lower() in {"write", "edit", "workspace_write"}:
            return PolicyResult(
                PolicyDecision.ALLOW_WITH_MONITORING,
                reasons=["Local workspace write/edit"],
            )

        return PolicyResult(
            PolicyDecision.REQUIRE_AUTO_REVIEW,
            reasons=["Default auto-review for unclassified action"],
        )


# Singleton for convenience
policy_engine = PolicyEngine()
