"""Independent authorization for the agent loop. Never consults the model.

Phase 3 additions:
- Argument provenance: identifying values for external_send / destructive / spend /
  navigation-to-new-origin must come from user-authored messages or approved context.
  Values that appear *only* in untrusted tool output force APPROVAL with provenance shown.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import structlog

from app.core.policy_engine import (
    PolicyContext,
    PolicyDecision as CoreDecision,
    PolicyResult,
    TrustLevel,
    policy_engine,
)
from app.runtime.tools import SideEffect, ToolSpec

logger = structlog.get_logger("onsite48.runtime.policy")


class Decision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    APPROVAL = "APPROVAL"
    TAKEOVER = "TAKEOVER"


@dataclass
class PolicyDecisionResult:
    decision: Decision
    reason: str
    core: Optional[PolicyResult] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "metadata": self.metadata,
        }


_DEFAULT_SIDE_EFFECT_POLICY: dict[SideEffect, Decision] = {
    "read": Decision.ALLOW,
    "write": Decision.ALLOW,
    "destructive": Decision.APPROVAL,
    "external_send": Decision.APPROVAL,
    "spend": Decision.APPROVAL,
}

# Argument keys that identify an external target / spend destination / destructive object.
_IDENTIFYING_KEYS = (
    "to", "recipient", "recipients", "email", "address", "cc", "bcc",
    "url", "href", "link", "destination", "origin",
    "account", "account_id", "from_account", "to_account",
    "amount", "value", "price", "currency",
    "user_id", "channel", "channel_id", "chat_id",
    "path",  # for destructive file ops outside workspace — checked separately
)


@dataclass
class OrgPolicyTable:
    side_effect_overrides: dict[str, str] = field(default_factory=dict)
    tool_overrides: dict[str, str] = field(default_factory=dict)
    deny_tools: set[str] = field(default_factory=set)
    auto_allow_send: set[str] = field(default_factory=set)
    pre_approved_tools: set[str] = field(default_factory=set)
    approved_contacts: set[str] = field(default_factory=set)  # lowercased emails/ids


def org_policy_from_schedule(schedule_authorization: dict[str, Any] | None) -> OrgPolicyTable:
    """Build an org policy table from a scheduled task's autonomy policy.

    A scheduled task runs unattended, so tools the operator pre-approved via
    ``additional_allowed_tools`` / ``allowed_custom_mcp_tools`` must be allowed
    without an interactive confirmation. Only the tool-level approval is waived;
    the argument-provenance guard still applies, so a pre-approved send/spend
    tool whose target value looks injected still requires confirmation and
    safely deadlocks the unattended run.
    """
    if not isinstance(schedule_authorization, dict):
        return OrgPolicyTable()
    pre_approved: set[str] = set()
    for key in ("additional_allowed_tools", "allowed_custom_mcp_tools"):
        for tool in schedule_authorization.get(key) or []:
            name = str(tool).strip()
            if name:
                pre_approved.add(name)
    return OrgPolicyTable(pre_approved_tools=pre_approved)


def _map_core(decision: CoreDecision) -> Decision:
    if decision in (CoreDecision.ALLOW, CoreDecision.ALLOW_WITH_MONITORING):
        return Decision.ALLOW
    if decision == CoreDecision.DENY:
        return Decision.DENY
    if decision == CoreDecision.REQUIRE_USER_APPROVAL:
        return Decision.APPROVAL
    if decision == CoreDecision.REQUIRE_TAKEOVER:
        return Decision.TAKEOVER
    # Auto-review is performed by the system, not the user: every
    # policy_decision is journaled for audit, so an unclassified
    # non-high-impact action runs under monitoring instead of
    # dead-ending the task on an approval the UI cannot resolve.
    if decision == CoreDecision.REQUIRE_AUTO_REVIEW:
        return Decision.ALLOW
    return Decision.APPROVAL


def _normalize(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, (list, tuple)):
        return " ".join(_normalize(v) for v in val)
    return str(val).strip().lower()


def collect_trusted_strings(run_state: dict[str, Any]) -> set[str]:
    """Strings that appeared in user-authored messages or approved context."""
    trusted: set[str] = set()
    goal = str(run_state.get("goal") or "")
    if goal:
        trusted.add(goal.lower())
        # token-ish splits
        for tok in goal.replace(",", " ").replace(";", " ").split():
            if len(tok) >= 3:
                trusted.add(tok.lower())

    for msg in run_state.get("messages") or []:
        role = msg.get("role")
        if role == "user":
            content = str(msg.get("content") or "")
            trusted.add(content.lower())
            for tok in content.replace(",", " ").replace(";", " ").split():
                if len(tok) >= 3:
                    trusted.add(tok.lower())

    # Explicit approved contacts from role_config / org
    for c in run_state.get("approved_contacts") or []:
        trusted.add(str(c).lower())
    for c in (run_state.get("role_config") or {}).get("approved_contacts") or []:
        trusted.add(str(c).lower())

    return trusted


def collect_untrusted_strings(run_state: dict[str, Any]) -> set[str]:
    """Strings that appeared only in untrusted tool outputs (provenance tracking)."""
    untrusted: set[str] = set()
    for msg in run_state.get("messages") or []:
        content = str(msg.get("content") or "")
        # wrap_untrusted marks content with a provenance header
        if "UNTRUSTED_DATA" in content or "provenance=" in content or msg.get("role") == "tool":
            for tok in content.replace(",", " ").replace(";", " ").replace('"', " ").split():
                if len(tok) >= 3 and not tok.startswith("["):
                    untrusted.add(tok.lower())
    # Explicit ledger of untrusted values collected by the loop
    for val in run_state.get("untrusted_values") or []:
        untrusted.add(str(val).lower())
    return untrusted


def check_argument_provenance(
    args: dict[str, Any],
    *,
    side: SideEffect,
    tool_name: str,
    run_state: dict[str, Any],
    org_policy: OrgPolicyTable,
) -> PolicyDecisionResult | None:
    """Return APPROVAL result if an identifying arg comes only from untrusted data.

    Structural check: value must appear in trusted strings OR approved contacts.
    If it appears only in untrusted tool output → APPROVAL with provenance.
    """
    if side not in ("external_send", "destructive", "spend") and "navigate" not in tool_name.lower():
        return None

    trusted = collect_trusted_strings(run_state)
    untrusted = collect_untrusted_strings(run_state)
    for c in org_policy.approved_contacts:
        trusted.add(c.lower())

    suspicious: list[dict[str, str]] = []
    for key in _IDENTIFYING_KEYS:
        if key not in args:
            continue
        raw = args[key]
        norm = _normalize(raw)
        if not norm or len(norm) < 3:
            continue
        # Present in user goal / user messages / approved contacts?
        in_trusted = any(norm in t or t in norm for t in trusted if len(t) >= 3)
        # Also check exact token membership
        if not in_trusted:
            in_trusted = norm in trusted
        in_untrusted = norm in untrusted or any(norm in u for u in untrusted if len(u) >= 3)

        if not in_trusted and in_untrusted:
            suspicious.append({
                "key": key,
                "value": str(raw)[:200],
                "provenance": "untrusted_tool_output_only",
            })
        elif not in_trusted and not in_untrusted:
            # Value appeared from nowhere the user wrote — still require approval for external_send/spend
            if side in ("external_send", "spend"):
                suspicious.append({
                    "key": key,
                    "value": str(raw)[:200],
                    "provenance": "not_in_user_context",
                })

    if suspicious:
        return PolicyDecisionResult(
            Decision.APPROVAL,
            "identifying argument provenance requires approval",
            metadata={"provenance": suspicious, "side_effect": side, "tool": tool_name},
        )
    return None


def evaluate(
    call: dict[str, Any],
    spec: ToolSpec,
    run_state: dict[str, Any],
    *,
    org_policy: OrgPolicyTable | None = None,
) -> PolicyDecisionResult:
    name = str(call.get("name") or spec.name)
    args = call.get("arguments") or {}
    org_policy = org_policy or OrgPolicyTable()

    if name in org_policy.deny_tools:
        return PolicyDecisionResult(Decision.DENY, f"org policy denies tool {name}")

    if name in org_policy.tool_overrides:
        try:
            return PolicyDecisionResult(
                Decision(org_policy.tool_overrides[name]),
                f"org tool override for {name}",
            )
        except ValueError:
            pass

    if name in ("finish", "ask_user"):
        return PolicyDecisionResult(Decision.ALLOW, "loop control tool")

    side = spec.side_effect

    # Allow workspace-local file_write/file_edit.
    # Unattended mode: fully allowed. Interactive mode: allowed with monitoring
    # so research workflows can write deliverables without blocking on approval.
    unattended = bool(
        run_state.get("unattended")
        or (run_state.get("role_config") or {}).get("unattended")
        or org_policy and getattr(org_policy, "unattended", False)
    )
    if name in ("file_write", "file_edit", "write_file", "edit_file"):
        import os
        from pathlib import Path as _P
        target = str((args or {}).get("path") or (args or {}).get("file") or "")
        ws = str(run_state.get("workspace") or os.environ.get("AGENT_WORKSPACE") or "")
        if target and ws:
            try:
                target_p = _P(target).resolve()
                ws_p = _P(ws).resolve()
                if target_p == ws_p or ws_p in target_p.parents:
                    # Destructive names still go through normal rules
                    base = target_p.name.lower()
                    if base not in (".env", "credentials.json", "id_rsa", "id_ed25519"):
                        if unattended:
                            return PolicyDecisionResult(
                                Decision.ALLOW,
                                f"unattended workspace write allowed for {name}",
                            )
                        return PolicyDecisionResult(
                            Decision.ALLOW,
                            f"workspace write allowed (monitored) for {name}",
                        )
            except Exception:
                pass

    # --- Phase 3: argument provenance (structural, not keyword matching on goals) ---
    prov = check_argument_provenance(
        args, side=side, tool_name=name, run_state=run_state, org_policy=org_policy
    )
    if prov is not None:
        return prov

    if side in ("external_send", "destructive", "spend"):
        if name in org_policy.auto_allow_send and side == "external_send":
            return PolicyDecisionResult(Decision.ALLOW, f"org auto-allow send for {name}")
        override = org_policy.side_effect_overrides.get(side)
        if override:
            try:
                d = Decision(override)
                if d != Decision.ALLOW:
                    return PolicyDecisionResult(d, f"org side_effect override {side}")
            except ValueError:
                pass
        else:
            if name in org_policy.pre_approved_tools:
                return PolicyDecisionResult(Decision.ALLOW, f"schedule pre-approved tool {name}")
            d = _DEFAULT_SIDE_EFFECT_POLICY[side]
            if d != Decision.ALLOW:
                return PolicyDecisionResult(d, f"default for side_effect={side}")

    if spec._approval_required and side != "read":
        if name in org_policy.pre_approved_tools:
            return PolicyDecisionResult(Decision.ALLOW, f"schedule pre-approved tool {name}")
        return PolicyDecisionResult(
            Decision.APPROVAL,
            f"tool {name} requires approval (side_effect={side})",
        )

    ctx = PolicyContext(
        user_id=str(run_state.get("user_id") or "") or None,
        task_id=str(run_state.get("task_id") or run_state.get("run_id") or "") or None,
        goal=str(run_state.get("goal") or ""),
        tool=name,
        action=name,
        resource=str((args or {}).get("resource") or ""),
        mutation_type="read" if (name == "shell_exec" and (args or {}).get("mutating") is False) else side,
        financial_impact=side == "spend",
        external_communication=side == "external_send",
        reversibility="irreversible" if side in ("external_send", "destructive", "spend") else "unknown",
        content_trust=TrustLevel.USER_INSTRUCTION,
        raw_content=str(args)[:2000],
        metadata={"side_effect": side, "idempotency": spec.idempotency},
    )
    try:
        core_result = policy_engine.evaluate(ctx)
        mapped = _map_core(core_result.decision)
        if mapped != Decision.ALLOW:
            return PolicyDecisionResult(
                mapped,
                "; ".join(core_result.reasons) or f"policy_engine={core_result.decision.value}",
                core=core_result,
            )
    except Exception as exc:
        logger.warning("runtime.policy.core_eval_failed", error=str(exc))
        if side in ("external_send", "destructive", "spend"):
            return PolicyDecisionResult(
                Decision.APPROVAL,
                f"policy engine error; requiring approval: {exc}",
            )
    return PolicyDecisionResult(Decision.ALLOW, f"allowed side_effect={side}")
