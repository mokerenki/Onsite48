"""Deterministic quality gates for model and shortcut plans.

The critic is intentionally conservative: it rejects only structural or semantic
failures that can be proven locally and reports warnings for ambiguous cases.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


@dataclass(frozen=True)
class PlanCritique:
    accepted: bool
    score: int
    confidence: dict[str, float]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    blockers: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "score": self.score,
            "confidence": dict(self.confidence),
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
        }


def _words(value: str) -> set[str]:
    return {item for item in re.findall(r"[a-z0-9_]+", value.lower()) if len(item) > 2}


def _is_mutating(step: dict[str, Any]) -> bool:
    """Whether a step claims to change external state.

    Derived from the declared idempotency class first, because that is the
    structured signal, and from the description only as a backstop for plans
    that omit it.
    """
    idempotency = str(step.get("idempotency") or "").strip().lower()
    if idempotency in {"non_idempotent_write", "write"}:
        return True
    if idempotency == "read_only":
        return False
    if step.get("approval_required") is True:
        return True
    # Anchored to the start of the description, because plan steps are written
    # in the imperative ("Delete the draft"). Matching anywhere in the string
    # flagged reads like "Find the draft I asked about" as mutations, purely on
    # the noun.
    return bool(re.match(
        r"\s*(send|delete|remove|archive|forward|create|update|rename|move|cancel|reply|draft|mark)\b",
        str(step.get("description") or ""), re.I,
    ))


# Postcondition types that can only inspect the shape of what a tool returned.
# They cannot tell whether an external system actually changed, so on a
# mutating step they are a claim of verification with no observation behind it.
_SHAPE_ONLY_VERIFICATIONS = {"field_exists", "schema_validation"}


def _is_tautological(postcondition: Any) -> bool:
    """A postcondition that cannot fail is worse than none at all.

    It converts an unchecked mutation into a step that reports itself verified.
    """
    if not isinstance(postcondition, dict):
        return True
    kind = str(postcondition.get("type") or "").strip().lower()
    expected = postcondition.get("expected")
    if not isinstance(expected, dict):
        expected = {}
    field = str(postcondition.get("field") or "").strip()

    if kind in {"equality", "field_equals", "contains", "absence", "field_exists", "entity_state", "browser_text", "browser_attribute", "entity_exists"}:
        if not field and not postcondition.get("entity_type"):
            return True
    if kind == "contains" and not str(expected.get("substring") or "").strip():
        return True
    if kind in {"equality", "field_equals"} and "value" not in expected:
        return True
    if kind == "browser_attribute" and not str(expected.get("name") or expected.get("attribute") or "").strip():
        return True
    if kind == "browser_dom" and not str(expected.get("selector") or "").strip():
        return True
    if kind == "browser_url" and not str(expected.get("url") or expected.get("pattern") or "").strip():
        return True
    if kind == "schema_validation" and not isinstance(expected.get("schema"), dict):
        return True
    if kind == "source_of_truth" and not str(postcondition.get("verifier_tool") or "").strip():
        return True
    return False


def critique_verification_semantics(plan: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Check that a step's verification could actually detect its own failure.

    Structural critique answers 'did every step declare a check?'. It cannot
    answer 'would that check notice if the step did nothing?'. A mutating step
    whose only postconditions inspect the shape of its own output will pass
    verification whether or not the mutation happened, which is how an unsent
    email gets reported as sent.
    """
    warnings: list[str] = []
    blockers: list[str] = []
    for step in plan or []:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("step_id") or step.get("id") or "unknown")
        postconditions = step.get("postconditions") or step.get("verification") or []
        if isinstance(postconditions, dict):
            postconditions = [postconditions]
        if not isinstance(postconditions, list):
            continue

        if not _is_mutating(step):
            continue

        if not postconditions:
            blockers.append(
                f"Step {step_id} changes external state but declares no verification."
            )
            continue

        if all(_is_tautological(item) for item in postconditions):
            blockers.append(
                f"Step {step_id} changes external state but every postcondition is "
                "tautological, so verification cannot fail."
            )
            continue

        kinds = {str(item.get("type") or "").strip().lower() for item in postconditions if isinstance(item, dict)}
        if kinds and kinds.issubset(_SHAPE_ONLY_VERIFICATIONS):
            warnings.append(
                f"Step {step_id} changes external state but is verified only by "
                "inspecting its own output shape, which cannot detect an "
                "external system that did not change."
            )
    return warnings, blockers


def critique_plan(goal: str, plan: list[dict[str, Any]], *, tool_registry: Any = None) -> PlanCritique:
    warnings: list[str] = []
    blockers: list[str] = []
    goal_words = _words(goal)
    if not plan:
        return PlanCritique(False, 0, {"intent": 0.0, "tool": 0.0, "safety": 0.0, "verification": 0.0}, blockers=("The plan is empty.",))

    tool_steps = [step for step in plan if step.get("tool_name")]
    descriptions = " ".join(str(step.get("description") or "") for step in plan)
    plan_words = _words(descriptions)
    intent_overlap = len(goal_words & plan_words) / max(1, len(goal_words))
    tool_confidence = 1.0 if all(step.get("tool_name") and (tool_registry is None or tool_registry.get(step["tool_name"])) for step in tool_steps) else 0.0
    verification_confidence = sum(bool(step.get("verification") or step.get("postconditions")) for step in plan) / max(1, len(plan))
    risky_steps = [step for step in plan if step.get("approval_required") or str(step.get("idempotency", "")).endswith("write")]
    unsafe_unapproved = [step for step in risky_steps if step.get("approval_required") is False and str(step.get("idempotency", "")) == "non_idempotent_write"]
    safety_confidence = 0.0 if unsafe_unapproved else 1.0

    seen: set[str] = set()
    for step in plan:
        step_id = str(step.get("step_id") or "")
        if not step_id:
            blockers.append("A plan step has no stable step_id.")
        if step_id in seen:
            blockers.append(f"Duplicate step_id: {step_id}.")
        seen.add(step_id)
        for dependency in step.get("depends_on") or []:
            if str(dependency) not in {str(item.get("step_id")) for item in plan}:
                blockers.append(f"Step {step_id} depends on unknown step {dependency}.")
        if step.get("tool_name") and not step.get("tool_args") and re.search(r"\b(to|from|query|url|id|message|subject)\b", str(step.get("description", "")), re.I):
            warnings.append(f"Step {step_id} may require arguments that are not explicit.")

    if intent_overlap < 0.08 and goal_words:
        warnings.append("Plan language has very low overlap with the user's goal.")
    if verification_confidence < 0.5:
        warnings.append("Some steps do not declare a verification rule or postcondition.")
    if unsafe_unapproved:
        blockers.append("A non-idempotent write step is missing explicit approval.")
    if not tool_steps and len(plan) > 0:
        warnings.append("Plan contains no tool action; confirm that synthesis-only execution is intentional.")

    semantic_warnings, semantic_blockers = critique_verification_semantics(plan)
    warnings.extend(semantic_warnings)
    blockers.extend(semantic_blockers)

    confidence = {
        "intent": round(min(1.0, max(0.0, intent_overlap)), 3),
        "tool": round(tool_confidence, 3),
        "safety": round(safety_confidence, 3),
        "verification": round(verification_confidence, 3),
    }
    score = round(100 * sum(confidence.values()) / len(confidence))
    accepted = not blockers and tool_confidence > 0.0
    return PlanCritique(accepted, score, confidence, tuple(dict.fromkeys(warnings)), tuple(dict.fromkeys(blockers)))


def grounded_answer_claims(answer: str, facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a lightweight grounding report for final-answer synthesis.

    This does not attempt to prove arbitrary prose. It detects whether an answer
    contains no verified facts at all and exposes the evidence IDs for the UI/audit.
    """
    verified = [fact for fact in facts if isinstance(fact, dict) and fact.get("verified") is True]
    return {
        "grounded": bool(verified) or not answer.strip(),
        "evidence_ids": [str(fact.get("evidence_id")) for fact in verified if fact.get("evidence_id")],
        "fact_count": len(verified),
        "warning": None if verified or not answer.strip() else "The answer has no verified evidence attached; present it as an uncertainty or perform verification before claiming completion.",
    }