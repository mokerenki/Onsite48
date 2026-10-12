"""Server-authoritative plan policy: the "code constrains" half of planning.

Planning used to be inverted. A large deterministic rule cascade in
``planner.py`` decided what the plan *was*, and the model was only consulted when
no rule matched. That made the regex layer the real brain, made every new user
phrasing a code change, and left the model free to invent tool arguments that
reached providers as immediate 400s.

The corrected division of labour is:

* the model proposes the plan (see ``Planner.generate_plan``);
* this module decides whether that proposal is *allowed to run*.

Every invariant here is a property the system must never violate regardless of
what a model produced, so the same policy is applied to model plans and to
deterministic plans. Applying it to both is deliberate: the deterministic layer
is a fast path and a repair candidate, not a privileged one.

Repairs are intentionally conservative. When a required argument is missing the
policy converts the step into an explicit clarification instead of inventing a
value, and when a destructive action has no unambiguous target it replaces the
action with a read-only lookup plus a question. Guessing here can send mail or
destroy data.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import structlog

logger = structlog.get_logger("onsite48.plan_policy")


# Mirrors executor.SCHEDULE_HARD_STOP_TOOLS. Duplicated deliberately: the
# executor is the enforcement point, this module must not import agent modules.
DESTRUCTIVE_TOOLS = frozenset({
    "gmail_send_message",
    "gmail_delete_draft",
    "gmail_delete_message",
    "gmail_trash_message",
    "slack_send_message",
    "github_create_issue",
    "atlassian_create_issue",
    "custom_api_mutate",
    "computer_click",
    "computer_click_at",
    "computer_fill",
    "computer_type_at",
})

# Arguments a provider rejects when absent or blank. Derived from provider
# behaviour, not from prompt wording, so a model cannot argue its way past them.
REQUIRED_NON_EMPTY_ARGS: Dict[str, Tuple[str, ...]] = {
    "gmail_create_draft": ("to", "subject", "body"),
    "gmail_delete_draft": ("draft_id",),
    "gmail_delete_message": ("message_id",),
    "gmail_trash_message": ("message_id",),
    "gmail_search_messages": ("query",),
    "gmail_send_message": ("to", "subject", "body"),
    "slack_send_message": ("channel", "text"),
    "github_create_issue": ("owner", "repo", "title"),
    "atlassian_create_issue": ("project", "title"),
}

# Alternatives that satisfy a tool even when its primary arguments are absent.
ARGUMENT_ALTERNATIVES: Dict[str, Tuple[Tuple[str, ...], ...]] = {
    # Sending an existing draft needs only its identifier; composing inline
    # needs the full message. Either shape is valid, neither may be empty.
    "gmail_send_message": (("draft_id",), ("to", "subject", "body")),
}

# Destructive tools whose target must be unambiguous. A reference such as
# "delete that draft" names no identifier, and resolving it to an arbitrary item
# can destroy the wrong one.
DESTRUCTIVE_TARGET_ARGS: Dict[str, str] = {
    "gmail_delete_draft": "draft_id",
    "gmail_delete_message": "message_id",
    "gmail_trash_message": "message_id",
}

# Read-only step used to enumerate candidates for an ambiguous destructive target.
AMBIGUOUS_TARGET_LOOKUP: Dict[str, Tuple[str, Dict[str, Any]]] = {
    "gmail_delete_draft": ("gmail_list_drafts", {"limit": 10}),
    "gmail_delete_message": ("gmail_search_messages", {"query": "in:inbox", "limit": 5}),
    "gmail_trash_message": ("gmail_search_messages", {"query": "in:inbox", "limit": 5}),
}

_MAIL_HINT = re.compile(
    r"\b(?:e-?mails?|gmail|mail|mailbox|inbox|outbox|unsent|drafts?|reply|forward|"
    r"send|sent|trash|subject|recipient|address|message|write|wrote|compose|composing|"
    r"@)\b",
    re.IGNORECASE,
)
_STEP_REF = re.compile(r"^\$steps\.[A-Za-z0-9_.\[\]-]+\.")


@dataclass
class PolicyReport:
    """The outcome of evaluating one plan against the policy."""

    accepted: bool
    plan: List[Dict[str, Any]] = field(default_factory=list)
    violations: List[Dict[str, str]] = field(default_factory=list)
    repairs: List[str] = field(default_factory=list)
    regraded: bool = False
    repaired: bool = False

    @property
    def blocking(self) -> List[Dict[str, str]]:
        return [violation for violation in self.violations if violation.get("severity") == "blocking"]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "regraded": self.regraded,
            "violations": self.violations,
            "repairs": self.repairs,
        }

    def describe(self) -> str:
        if not self.violations:
            return "ok"
        return "; ".join(
            f"{item.get('code', 'violation')}({item.get('step_id', '-')}): {item.get('detail', '')}"
            for item in self.violations
        )


def _violation(code: str, step_id: str, detail: str, severity: str = "blocking") -> Dict[str, str]:
    return {"code": code, "step_id": step_id, "detail": detail, "severity": severity}


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _is_step_reference(value: Any) -> bool:
    """A value produced by an earlier step is legitimately unknown up front."""
    return isinstance(value, str) and bool(_STEP_REF.match(value.strip()))


def _missing_required_args(tool_name: str, tool_args: Dict[str, Any]) -> List[str]:
    required = REQUIRED_NON_EMPTY_ARGS.get(tool_name, ())
    alternatives = ARGUMENT_ALTERNATIVES.get(tool_name, ())
    if alternatives:
        for group in alternatives:
            if all(
                _has_value(tool_args.get(key)) or _is_step_reference(tool_args.get(key))
                for key in group
            ):
                return []
        return ["|".join(group) for group in alternatives]
    return [
        key
        for key in required
        if not (_has_value(tool_args.get(key)) or _is_step_reference(tool_args.get(key)))
    ]


def clarification_step(
    step_id: str,
    question: str,
    *,
    description: str = "Ask the user for the missing information before acting.",
    depends_on: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    return {
        "step_id": step_id,
        "description": description,
        "tool_name": None,
        "tool_args": {"question": question},
        "expected_outcome": "The user supplies the missing information before any state is changed.",
        "on_failure": "pause",
        "idempotency": "read_only",
        "depends_on": list(depends_on or []),
    }


def confirmation_step(
    step_id: str,
    tool_name: str,
    tool_args: Dict[str, Any],
    *,
    depends_on: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    return {
        "step_id": step_id,
        "description": f"Show the user exactly what '{tool_name}' will do and wait for explicit approval.",
        "tool_name": None,
        "tool_args": {
            "question": (
                f"I am about to run {tool_name} with {json.dumps(tool_args, default=str)}. "
                "Confirm to proceed."
            )
        },
        "expected_outcome": "The user explicitly approves the specific irreversible action.",
        "on_failure": "pause",
        "idempotency": "read_only",
        "depends_on": list(depends_on or []),
    }


def _sanitize_arguments(
    step: Dict[str, Any], schema_parameters: Optional[Dict[str, Any]]
) -> List[str]:
    """Drop arguments the tool contract does not declare.

    Unknown arguments are a leading cause of provider 400s and are never a
    legitimate way to pass extra context, so they are removed rather than
    forwarded.
    """
    if not schema_parameters:
        return []
    tool_args = step.get("tool_args")
    if not isinstance(tool_args, dict):
        return []
    unknown = [key for key in tool_args if key not in schema_parameters]
    if not unknown:
        return []
    step["tool_args"] = {key: value for key, value in tool_args.items() if key in schema_parameters}
    return unknown


def _schema_parameters(tool: Any) -> Optional[Dict[str, Any]]:
    schema = getattr(tool, "schema", None)
    parameters = getattr(schema, "parameters", None)
    return parameters if isinstance(parameters, dict) else None


def evaluate_plan(
    plan: Sequence[Dict[str, Any]],
    *,
    goal: str,
    tool_registry: Any,
    intent: Any | None = None,
    regrade: bool = True,
    insert_confirmations: bool = False,
) -> PolicyReport:
    """Check a proposed plan and repair what can be repaired without guessing."""
    steps = [dict(step) for step in plan if isinstance(step, dict)]
    report = PolicyReport(accepted=True, plan=steps)
    if not steps:
        report.accepted = False
        report.violations.append(_violation("empty_plan", "-", "the plan contains no steps"))
        return report

    seen_ids: set[str] = set()
    rebuilt: List[Dict[str, Any]] = []

    for index, step in enumerate(steps):
        step_id = str(step.get("step_id") or f"step_{index + 1}")
        step["step_id"] = step_id
        if step_id in seen_ids:
            step["step_id"] = f"{step_id}_{index + 1}"
            report.repairs.append(f"renamed duplicate step_id {step_id}")
        seen_ids.add(step["step_id"])

        tool_name = step.get("tool_name")
        tool_name = str(tool_name).strip() if tool_name is not None else None
        step["tool_name"] = tool_name or None
        if not isinstance(step.get("tool_args"), dict):
            step["tool_args"] = {}

        if not tool_name:
            rebuilt.append(step)
            continue

        tool = tool_registry.get(tool_name)
        if tool is None:
            report.accepted = False
            report.violations.append(
                _violation("unknown_tool", step_id, f"'{tool_name}' is not a registered tool")
            )
            rebuilt.append(step)
            continue

        unknown_args = _sanitize_arguments(step, _schema_parameters(tool))
        if unknown_args:
            report.repairs.append(
                f"removed undeclared arguments {sorted(unknown_args)} from {step_id}"
            )
            report.violations.append(
                _violation("unknown_argument", step_id, f"removed {sorted(unknown_args)}", severity="repaired")
            )

        # A destructive action with no unambiguous target is never executed.
        target_arg = DESTRUCTIVE_TARGET_ARGS.get(tool_name)
        if target_arg:
            target = step["tool_args"].get(target_arg)
            if not _has_value(target) and not _is_step_reference(target):
                lookup = AMBIGUOUS_TARGET_LOOKUP.get(tool_name)
                if lookup is None or tool_registry.get(lookup[0]) is None:
                    report.accepted = False
                    report.violations.append(
                        _violation(
                            "ambiguous_destructive_target",
                            step_id,
                            f"{tool_name} has no {target_arg} and no lookup tool is available",
                        )
                    )
                    rebuilt.append(step)
                    continue
                lookup_tool, lookup_args = lookup
                rebuilt.append({
                    "step_id": f"{step_id}-lookup",
                    "description": f"List the candidate items so the user can identify the exact {target_arg}.",
                    "tool_name": lookup_tool,
                    "tool_args": dict(lookup_args),
                    "expected_outcome": f"The candidate items are listed with their {target_arg} values.",
                    "on_failure": "fail",
                    "idempotency": "read_only",
                })
                rebuilt.append(clarification_step(
                    f"{step_id}-clarify",
                    f"Which item should I act on? I will list the candidates with their {target_arg} values "
                    "so you can pick the exact one.",
                    depends_on=[f"{step_id}-lookup"],
                ))
                report.repaired = True
                report.repairs.append(
                    f"replaced {step_id} with a read-only lookup plus clarification"
                )
                report.violations.append(
                    _violation(
                        "ambiguous_destructive_target",
                        step_id,
                        f"{tool_name} was requested without an explicit {target_arg}",
                        severity="repaired",
                    )
                )
                continue

        # Required provider arguments are never invented.
        missing = _missing_required_args(tool_name, step["tool_args"])
        if missing and regrade:
            question = (
                f"I need a little more detail before I can run {tool_name}: "
                f"{', '.join(missing)} is missing or empty. What should I use?"
            )
            rebuilt.append(clarification_step(
                f"{step_id}-clarify",
                question,
                depends_on=list(step.get("depends_on") or []),
            ))
            report.regraded = True
            report.repairs.append(f"converted {step_id} to a clarification for {missing}")
            report.violations.append(
                _violation(
                    "missing_required_argument",
                    step_id,
                    f"{tool_name} requires {missing}",
                    severity="repaired",
                )
            )
            continue

        if missing:
            report.accepted = False
            report.violations.append(
                _violation("missing_required_argument", step_id, f"{tool_name} requires {missing}")
            )

        if tool_name in DESTRUCTIVE_TOOLS:
            report.violations.append(
                _violation("irreversible_action", step_id, f"{tool_name} changes external state", severity="notice")
            )
            if insert_confirmations and not _has_clarification_before(rebuilt):
                # The executor still enforces the approval gate; this makes the
                # irreversible step visible and previewable before it runs.
                confirmation_id = f"{step_id}-confirm"
                rebuilt.append(confirmation_step(
                    confirmation_id, tool_name, dict(step["tool_args"]),
                    depends_on=list(step.get("depends_on") or []),
                ))
                step["depends_on"] = [*list(step.get("depends_on") or []), confirmation_id]
                report.repairs.append(f"inserted a confirmation preview before {step_id}")
            rebuilt.append(step)
            continue

        rebuilt.append(step)

    report.plan = _resolve_dependencies(rebuilt, report)
    if not _mentions_mail(goal) and _uses_mail_write_tool(rebuilt):
        # Advisory only. The typed intent contract remains the authoritative
        # capability check; a lexical signal is too coarse to block on, and a
        # false positive here would refuse a legitimate request.
        report.violations.append(
            _violation(
                "scope_leak",
                "-",
                "the plan writes to a mail account for a goal that never mentions mail",
                severity="warning",
            )
        )
    return report


def _resolve_dependencies(steps: List[Dict[str, Any]], report: PolicyReport) -> List[Dict[str, Any]]:
    known = {str(step.get("step_id")) for step in steps}
    prior_step_id: Optional[str] = None
    for step in steps:
        # An absent dependency list means "after the previous step", matching the
        # plan contract. An explicit empty list means "may run in parallel".
        if "depends_on" not in step:
            step["depends_on"] = [prior_step_id] if prior_step_id else []
        depends_on = step.get("depends_on")
        if not isinstance(depends_on, list):
            step["depends_on"] = []
            report.repairs.append(f"coerced non-list dependencies on {step.get('step_id')}")
            prior_step_id = str(step.get("step_id"))
            continue
        resolved = [str(value) for value in depends_on if str(value) in known and str(value) != step.get("step_id")]
        if len(resolved) != len([value for value in depends_on]):
            report.repairs.append(f"dropped dangling dependencies on {step.get('step_id')}")
        step["depends_on"] = resolved
        prior_step_id = str(step.get("step_id"))
    return steps


def _mentions_mail(goal: str) -> bool:
    return bool(_MAIL_HINT.search(goal or ""))


# ─── Replan validation ─────────────────────────────────────────────
#
# A replan is proposed by the same model, under less pressure, and usually in
# response to a failure -- exactly the conditions under which it is most likely
# to re-issue an action that already succeeded. It therefore gets the same
# pipeline as an initial plan (see `Planner.generate_plan`), plus two checks an
# initial plan does not need because it runs against an empty ledger:
#
# * **verified work is preserved**, and a step that merely repeats it is dropped
#   rather than re-executed;
# * **a repeated irreversible or non-idempotent action is rejected**, because
#   re-sending a mail or re-creating a draft is not a retry, it is a second
#   action the user never asked for.

#: A replan longer than this is not a recovery, it is a restart. The bound is
#: tighter than `validate_model_plan_steps` because a recovery plan should be the
#: smallest missing transition, and an unbounded one is a cheap way for a model
#: to exhaust the task budget on a task that had already finished.
MAX_REPLAN_STEPS = 24

#: Feedback keys that constitute explicit evidence that repeating a
#: non-idempotent action is safe. A postcondition verifier that re-observed the
#: world and reported the action had no effect is evidence; the model asserting
#: it in prose is not, because "evidence" here is a field the runtime produced.
_EVIDENCE_KEYS = ("observed_state", "evidence", "action_had_no_effect", "prior_attempt_no_effect")


def action_fingerprint(tool_name: str, tool_args: Dict[str, Any]) -> str:
    """Stable identity of one external action.

    Used to recognise a repeated action across plans and across the durable
    result ledger. Argument order is normalized so a reordering is not read as a
    different action.
    """
    try:
        serialized = json.dumps(tool_args or {}, sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover - default=str prevents this
        serialized = str(sorted((tool_args or {}).keys()))
    return f"{tool_name}:{serialized}"


def completed_action_fingerprints(results: Any) -> Dict[str, str]:
    """Fingerprints of actions already verified as complete.

    A result counts as verified only when it is a dict, names a tool, and is not
    explicitly failed. ``state["results"]`` mixes step outcomes, so treating a
    recorded failure as completed work would let the agent skip a step that
    actually still needs doing.
    """
    fingerprints: Dict[str, str] = {}
    if not isinstance(results, list):
        return fingerprints
    for entry in results:
        if not isinstance(entry, dict):
            continue
        tool_name = entry.get("tool")
        if not tool_name:
            continue
        if entry.get("failure") or str(entry.get("status") or "").lower() in {"failed", "error"}:
            continue
        fingerprint = action_fingerprint(str(tool_name), entry.get("tool_args") or {})
        # Keep the first: a later record for the same action carries no more
        # evidence than the first successful one.
        fingerprints.setdefault(fingerprint, str(entry.get("step") or tool_name))
    return fingerprints


def _is_non_idempotent(tool_name: str, tool: Any) -> bool:
    schema = getattr(tool, "schema", None)
    declared = getattr(schema, "idempotency", None)
    if declared:
        return str(declared) not in {"read_only", "idempotent_write"}
    if bool(getattr(schema, "irreversible", False)):
        return True
    return tool_name in DESTRUCTIVE_TOOLS


def dependency_report(steps: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Report duplicate ids, dangling dependencies, and cycles in one pass.

    `normalize_plan` rejects all three, but as one Pydantic message. The replan
    path needs to say *which* invariant broke so the recovery feedback is
    actionable for the next attempt instead of being re-derived from a string.
    """
    ids: List[str] = []
    duplicates: List[str] = []
    for step in steps:
        step_id = str(step.get("step_id") or "")
        if step_id in ids:
            duplicates.append(step_id)
        ids.append(step_id)
    known = set(ids)
    dangling: List[Tuple[str, str]] = []
    graph: Dict[str, set[str]] = {}
    for step in steps:
        step_id = str(step.get("step_id") or "")
        dependencies = step.get("depends_on") or []
        if not isinstance(dependencies, list):
            dangling.append((step_id, "depends_on is not a list"))
            graph.setdefault(step_id, set())
            continue
        resolved = {str(value) for value in dependencies}
        for missing in sorted(resolved - known):
            dangling.append((step_id, missing))
        graph[step_id] = {value for value in resolved if value in known and value != step_id}

    cycles: List[List[str]] = []
    visiting: List[str] = []
    visited: set[str] = set()

    def visit(step_id: str, trail: List[str]) -> None:
        if step_id in visited:
            return
        if step_id in visiting:
            start = visiting.index(step_id)
            cycles.append(trail[trail.index(step_id):] + [step_id])
            return
        visiting.append(step_id)
        for dependency in sorted(graph.get(step_id, set())):
            visit(dependency, visiting + [dependency] if dependency not in visiting else trail + [dependency])
        visiting.pop()
        visited.add(step_id)

    for step_id in graph:
        visit(step_id, [step_id])
    return {"duplicates": duplicates, "dangling": dangling, "cycles": cycles}


def _evidence_proves_safe(feedback: Any) -> str | None:
    """Return the evidence field that authorises a repeat, if there is one."""
    if not isinstance(feedback, Mapping):
        return None
    for key in _EVIDENCE_KEYS:
        value = feedback.get(key)
        if value:
            return key
    verification = feedback.get("verification")
    if isinstance(verification, Mapping) and verification.get("action_had_no_effect"):
        return "verification.action_had_no_effect"
    return None


def screen_repeated_actions(
    steps: Sequence[Dict[str, Any]],
    *,
    results: Any,
    tool_registry: Any,
    feedback: Any = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Drop verified work from a replan and reject unsafe repeats.

    Three outcomes for a step whose action already appears in the verified
    ledger:

    * **read-only** -- dropped. Re-running it costs money and time and changes
      nothing; the earlier result is still the answer.
    * **idempotent write** -- dropped, for the same reason.
    * **non-idempotent / irreversible** -- rejected as a blocking violation
      unless :func:`_evidence_proves_safe` finds runtime-produced evidence that
      the earlier attempt had no effect. The step is removed rather than left to
      run, because leaving it in place and merely labelling it is how a duplicate
      irreversible action gets executed after an approval prompt that does not
      mention the duplication.

    Returns the surviving steps plus the violations, so the caller can report
    exactly what was refused and why.
    """
    survivors: List[Dict[str, Any]] = []
    violations: List[Dict[str, str]] = []
    completed = completed_action_fingerprints(results)
    if not completed:
        return [dict(step) for step in steps], violations
    evidence_key = _evidence_proves_safe(feedback)
    for step in steps:
        tool_name = str(step.get("tool_name") or "")
        if not tool_name:
            survivors.append(dict(step))
            continue
        fingerprint = action_fingerprint(tool_name, step.get("tool_args") or {})
        if fingerprint not in completed:
            survivors.append(dict(step))
            continue
        step_id = str(step.get("step_id") or "-")
        tool = tool_registry.get(tool_name) if tool_registry is not None else None
        if not _is_non_idempotent(tool_name, tool):
            # Already verified, and repeating it changes nothing. The earlier
            # result is still the answer, so drop it instead of paying for it
            # again.
            violations.append(_violation(
                "verified_work_already_complete",
                step_id,
                f"{tool_name} was already completed and re-running it changes nothing",
                severity="notice",
            ))
            continue
        if evidence_key is None:
            violations.append(_violation(
                "repeated_irreversible_action",
                step_id,
                f"{tool_name} was already completed and is not safe to repeat; "
                "no evidence showed the earlier attempt had no effect",
            ))
            continue
        # Runtime evidence authorises exactly one retry of a non-idempotent
        # action, so the step stays -- this is the corrected transition, not a
        # duplicate -- and the record says so.
        violations.append(_violation(
            "verified_repeat_authorised_by_evidence",
            step_id,
            f"{tool_name} is retried once because runtime evidence ({evidence_key}) "
            "showed the earlier attempt had no effect",
            severity="notice",
        ))
        survivors.append(dict(step))
    return survivors, violations


def rebase_dependencies(steps: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Make dependencies point only at surviving steps.

    Screening can remove a step that others depended on. Leaving the reference in
    place produces a dangling dependency, which `normalize_plan` rejects -- so
    the replan would fail for a reason the policy created rather than one the
    model did. Dependencies on removed steps are dropped; the surviving order is
    preserved, and a step whose dependencies became empty runs after the previous
    survivor, which is the plan contract's meaning of an absent dependency.
    """
    known = {str(step.get("step_id")) for step in steps}
    rebased: List[Dict[str, Any]] = []
    for step in steps:
        entry = dict(step)
        dependencies = entry.get("depends_on")
        if isinstance(dependencies, list):
            entry["depends_on"] = [
                str(value) for value in dependencies
                if str(value) in known and str(value) != entry.get("step_id")
            ]
        rebased.append(entry)
    return rebased


def _has_clarification_before(steps: Sequence[Dict[str, Any]]) -> bool:
    """True when the plan already surfaces a question or preview to the user."""
    for step in steps:
        if step.get("tool_name") is None and step.get("tool_args"):
            return True
    return False


def _uses_mail_write_tool(steps: Sequence[Dict[str, Any]]) -> bool:
    return any(
        str(step.get("tool_name") or "") in {
            "gmail_create_draft", "gmail_send_message", "gmail_delete_draft",
            "gmail_delete_message", "gmail_trash_message",
        }
        for step in steps
    )


# ─── Structured plan output ───────────────────────────────────────

PLAN_JSON_SCHEMA: Dict[str, Any] = {
    "name": "execution_plan",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["steps"],
        "properties": {
            "steps": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["step_id", "description", "tool_name", "tool_args", "expected_outcome"],
                    "properties": {
                        "step_id": {
                            "type": "string",
                            "description": "Stable unique identifier for this step.",
                        },
                        "description": {
                            "type": "string",
                            "description": "What this single step does.",
                        },
                        "tool_name": {
                            "type": ["string", "null"],
                            "description": "Exact registered tool name, or null for synthesis and clarification.",
                        },
                        "tool_args": {
                            "type": "object",
                            "description": "Arguments matching the tool schema exactly. Omit unknown keys.",
                        },
                        "expected_outcome": {
                            "type": "string",
                            "description": "Observable result that proves the step succeeded.",
                        },
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Step ids that must complete first.",
                        },
                    },
                },
            }
        },
    },
}


def coerce_model_plan(payload: Any) -> List[Dict[str, Any]]:
    """Read a schema-shaped planning response into the runtime step shape.

    Accepts the ``{"steps": [...]}`` object produced under a JSON schema, a bare
    array, and a single object, so a provider that relaxes the constraint still
    produces a usable plan.
    """
    if isinstance(payload, dict):
        steps = payload.get("steps")
        if steps is None and ("tool_name" in payload or "step_id" in payload):
            steps = [payload]
    else:
        steps = payload
    if isinstance(steps, dict):
        steps = [steps]
    if not isinstance(steps, list):
        raise ValueError("The planning response did not contain a list of steps.")

    coerced: List[Dict[str, Any]] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        tool_args = step.get("tool_args")
        normalized = {
            "step_id": str(step.get("step_id") or f"step_{index + 1}"),
            "description": str(step.get("description") or "Complete the requested work."),
            "tool_name": (str(step["tool_name"]).strip() or None) if step.get("tool_name") else None,
            "tool_args": tool_args if isinstance(tool_args, dict) else {},
            "expected_outcome": str(step.get("expected_outcome") or ""),
        }
        if "depends_on" in step and step.get("depends_on") is not None:
            normalized["depends_on"] = [str(value) for value in step.get("depends_on") or []]
        coerced.append(normalized)
    if not coerced:
        raise ValueError("The planning response contained no usable steps.")
    return coerced


__all__ = [
    "DESTRUCTIVE_TOOLS",
    "MAX_REPLAN_STEPS",
    "REQUIRED_NON_EMPTY_ARGS",
    "ARGUMENT_ALTERNATIVES",
    "DESTRUCTIVE_TARGET_ARGS",
    "PLAN_JSON_SCHEMA",
    "PolicyReport",
    "action_fingerprint",
    "clarification_step",
    "completed_action_fingerprints",
    "confirmation_step",
    "coerce_model_plan",
    "dependency_report",
    "evaluate_plan",
    "rebase_dependencies",
    "screen_repeated_actions",
]