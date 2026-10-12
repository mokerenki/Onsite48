"""Strict model-plan schemas used at the planner boundary."""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.llm_json import parse_llm_json


class ModelPlanStep(BaseModel):
    model_config = ConfigDict(extra="allow")

    step_id: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2000)
    tool_name: str | None = None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    expected_outcome: str | None = None


class ModelPlanSubGoal(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2000)
    steps: list[ModelPlanStep] = Field(default_factory=list)
    success_condition: str | None = None


class ModelPlanPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    refined_goal: str | None = None
    ambiguous: bool = False
    clarification_needed: str | None = None
    clarification_question: str | None = None
    sub_goals: list[ModelPlanSubGoal] = Field(default_factory=list)
    success_conditions: list[str] = Field(default_factory=list)
    estimated_steps: int = Field(default=0, ge=0)
    requires_approval: bool = False
    plan: list[ModelPlanStep] = Field(default_factory=list)


def parse_and_validate_plan_payload(content: str) -> dict[str, Any]:
    raw = parse_llm_json(content)
    if not isinstance(raw, dict):
        raise ValueError("Planner response must be a JSON object.")
    try:
        parsed = ModelPlanPayload.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Planner response failed schema validation: {exc}") from exc
    return parsed.model_dump(exclude_none=True)


def extract_plan_steps(content: str) -> list[dict[str, Any]]:
    """Extract a canonical step list from a model response.

    Providers commonly wrap the requested array in ``plan``, ``steps``, or
    ``actions``. The old callers iterated over wrapper keys and converted the
    resulting exception into an empty plan, creating a needless replan loop.
    """
    raw = parse_llm_json(content)
    if isinstance(raw, dict):
        for key in ("plan", "steps", "actions"):
            candidate = raw.get(key)
            if isinstance(candidate, list):
                raw = candidate
                break
        else:
            if "tool_name" in raw or "step_id" in raw:
                raw = [raw]

    if not isinstance(raw, list) or not raw:
        raise ValueError("Planner response must contain a non-empty list of steps.")
    if not all(isinstance(step, dict) for step in raw):
        raise ValueError("Every planner step must be a JSON object.")
    return [dict(step) for step in raw]


def validate_model_plan_steps(steps: list[dict[str, Any]], *, max_steps: int = 64) -> list[dict[str, Any]]:
    """Apply cheap graph invariants before the expensive policy/executor path.

    The policy layer remains authoritative, but malformed model graphs should be
    rejected at the boundary: duplicate identifiers make event correlation
    ambiguous, dangling dependencies can deadlock execution, and unbounded
    proposals are an easy way for a prompt to exhaust task budgets.
    """
    if not steps:
        raise ValueError("Planner response must contain a non-empty list of steps.")
    if len(steps) > max_steps:
        raise ValueError(f"Planner response contains {len(steps)} steps; maximum is {max_steps}.")
    ids: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("Every planner step must be an object.")
        step_id = str(step.get("step_id") or "").strip()
        if not step_id:
            raise ValueError("Every planner step must have a step_id.")
        if step_id in ids:
            raise ValueError(f"Planner response contains duplicate step_id: {step_id}")
        ids.append(step_id)
    known = set(ids)
    for step in steps:
        dependencies = step.get("depends_on") or []
        if not isinstance(dependencies, list):
            raise ValueError(f"depends_on must be a list for step {step['step_id']}.")
        dangling = [str(item) for item in dependencies if str(item) not in known]
        if dangling:
            raise ValueError(f"Step {step['step_id']} depends on unknown step(s): {', '.join(dangling)}")
        if str(step["step_id"]) in {str(item) for item in dependencies}:
            raise ValueError(f"Step {step['step_id']} cannot depend on itself.")
    return steps