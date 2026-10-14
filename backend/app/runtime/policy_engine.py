"""YAML-based policy engine for org-level trust and approval rules.

This layer sits above the core runtime policy (policy.py) and allows operators
to define org-level policies as YAML files. The engine loads policies at startup
and evaluates them against tool calls, converting decisions into the same
PolicyDecisionResult format used by the runtime loop.

Policy YAML schema (policy.yaml):
```yaml
org_id: "default"
version: "1.0"
trust_levels:
  user_instruction: allow
  tool_result: monitor
  web_content: deny
rules:
  - name: "auto-approve pre-approved tools"
    when:
      tool_in: ["file_read", "search_memory"]
      trust: "user_instruction"
    action: "allow"
  - name: "require approval for high impact"
    when:
      tool_matches: ["send_*", "transfer_*", "purchase_*"]
    action: "require_approval"
    with:
      reason: "High-impact mutation requires human oversight"
  - name: "deny dangerous navigation"
    when:
      tool: "navigate"
      arg_path: "url"
      url_matches: ["file://*", "chrome-extension://*"]
    action: "deny"
```
"""
from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
import structlog
import yaml

from app.runtime.policy_types import Decision, PolicyDecisionResult
from app.core.policy_engine import TrustLevel

logger = structlog.get_logger("onsite48.runtime.policy_engine")


@dataclass
class PolicyRule:
    name: str
    when: dict[str, Any]
    action: str
    with_: dict[str, Any] = field(default_factory=dict)
    priority: int = 0


class YamlPolicyEngine:
    """Loads and evaluates YAML-based org policies."""

    def __init__(self, policies_dir: str | None = None):
        self.policies_dir = Path(
            policies_dir or os.environ.get("POLICY_DIR", "/etc/onsite48/policies")
        )
        self._policies: dict[str, dict[str, Any]] = {}
        self._rules_by_org: dict[str, list[PolicyRule]] = {}

    def load_policies(self) -> None:
        """Load all YAML policy files from the policies directory."""
        if not self.policies_dir.exists():
            logger.info("policy_engine.no_policies_dir", path=str(self.policies_dir))
            return

        loaded = 0
        for yaml_file in sorted(self.policies_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
                if data and isinstance(data, dict):
                    org_id = data.get("org_id", "default")
                    self._policies[org_id] = data
                    self._rules_by_org[org_id] = self._parse_rules(data.get("rules", []))
                    loaded += 1
                    logger.info("policy_engine.loaded_policy", file=yaml_file.name, org_id=org_id)
            except Exception as exc:
                logger.error("policy_engine.load_error", file=yaml_file.name, error=str(exc))

        logger.info("policy_engine.policies_loaded", count=loaded, dir=str(self.policies_dir))

    def _parse_rules(self, raw_rules: list[Any]) -> list[PolicyRule]:
        rules = []
        for raw in raw_rules or []:
            if not isinstance(raw, dict):
                continue
            when = raw.get("when", {}) or {}
            action = raw.get("action", "allow")
            rule = PolicyRule(
                name=raw.get("name", "unnamed"),
                when=when,
                action=action,
                with_={k: v for k, v in raw.items() if k not in ("name", "when", "action", "priority")},
                priority=int(raw.get("priority", 0)),
            )
            rules.append(rule)
        rules.sort(key=lambda r: r.priority, reverse=True)
        return rules

    def _matches_tool(self, tool_name: str, condition: Any) -> bool:
        """Check if tool_name matches the condition (string, list, or pattern)."""
        if condition is None:
            return True
        if isinstance(condition, str):
            if "*" in condition or "?" in condition:
                return fnmatch.fnmatch(tool_name, condition)
            return tool_name == condition
        if isinstance(condition, list):
            return any(self._matches_tool(tool_name, item) for item in condition)
        return False

    def _matches_arg(self, args: dict[str, Any], key: str, pattern: str) -> bool:
        """Check if args[key] matches a glob pattern."""
        val = args.get(key)
        if val is None:
            return False
        return fnmatch.fnmatch(str(val), pattern)

    def _check_rule_conditions(
        self,
        rule: PolicyRule,
        tool_name: str,
        args: dict[str, Any],
        run_state: dict[str, Any],
    ) -> bool:
        """Evaluate a rule's 'when' conditions against the current tool call."""
        when = rule.when
        if not when:
            return True

        # Tool name check
        if "tool" in when:
            if not self._matches_tool(tool_name, when["tool"]):
                return False
        if "tool_in" in when:
            if not self._matches_tool(tool_name, when["tool_in"]):
                return False
        if "tool_matches" in when:
            if not self._matches_tool(tool_name, when["tool_matches"]):
                return False

        # Trust level check
        if "trust" in when:
            expected_trust = when["trust"]
            actual_trust = run_state.get("content_trust", TrustLevel.USER_INSTRUCTION.value)
            if expected_trust != actual_trust:
                return False

        # Argument pattern checks
        for key, pattern in when.items():
            if key.startswith("arg_"):
                arg_name = key[4:]
                if isinstance(pattern, str) and ("*" in pattern or "?" in pattern):
                    if not self._matches_arg(args, arg_name, pattern):
                        return False
                elif isinstance(pattern, list):
                    if not any(self._matches_arg(args, arg_name, p) for p in pattern):
                        return False
                else:
                    if str(args.get(arg_name, "")) != str(pattern):
                        return False
            elif key.endswith("_matches"):
                arg_name = key[:-len("_matches")]
                if isinstance(pattern, str):
                    if not self._matches_arg(args, arg_name, pattern):
                        return False
                elif isinstance(pattern, list):
                    if not any(self._matches_arg(args, arg_name, p) for p in pattern):
                        return False

        # Side effect check
        if "side_effect" in when:
            spec = run_state.get("_tool_spec")
            if spec and spec.side_effect != when["side_effect"]:
                return False

        # Unattended mode check
        if "unattended" in when:
            val = bool(when["unattended"])
            actual = bool(
                run_state.get("unattended")
                or (run_state.get("role_config") or {}).get("unattended")
            )
            if actual != val:
                return False

        return True

    def evaluate(
        self,
        tool_name: str,
        args: dict[str, Any],
        run_state: dict[str, Any],
        *,
        org_id: str = "default",
    ) -> Optional[PolicyDecisionResult]:
        """Evaluate YAML policies and return a decision if one matches."""
        rules = self._rules_by_org.get(org_id, [])
        if not rules:
            return None

        for rule in rules:
            if self._check_rule_conditions(rule, tool_name, args, run_state):
                action = rule.action.lower()
                metadata = dict(rule.with_)
                if action == "allow":
                    decision = Decision.ALLOW
                    reason = rule.with_.get("reason", f"YAML policy '{rule.name}' allows")
                elif action == "deny":
                    decision = Decision.DENY
                    reason = rule.with_.get("reason", f"YAML policy '{rule.name}' denies")
                elif action in ("require_approval", "approval", "require_user_approval"):
                    decision = Decision.APPROVAL
                    reason = rule.with_.get("reason", f"YAML policy '{rule.name}' requires approval")
                elif action in ("require_takeover", "takeover"):
                    decision = Decision.TAKEOVER
                    reason = rule.with_.get("reason", f"YAML policy '{rule.name}' requires takeover")
                else:
                    logger.warning("policy_engine.unknown_action", rule=rule.name, action=action)
                    continue

                return PolicyDecisionResult(
                    decision=decision,
                    reason=reason,
                    metadata={"policy_rule": rule.name, **metadata},
                )

        return None

    def reload(self) -> None:
        """Reload all policies from disk (call on SIGHUP or admin request)."""
        self._policies.clear()
        self._rules_by_org.clear()
        self.load_policies()


# Module-level singleton loaded at app startup
_policy_engine: Optional[YamlPolicyEngine] = None


def get_policy_engine() -> YamlPolicyEngine:
    """Get the singleton policy engine instance."""
    global _policy_engine
    if _policy_engine is None:
        _policy_engine = YamlPolicyEngine()
        _policy_engine.load_policies()
    return _policy_engine
