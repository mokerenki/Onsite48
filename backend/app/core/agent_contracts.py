"""Typed execution contracts for planning and tool policy.

The agent still stores graph state as JSON-compatible dictionaries, but every plan
now crosses this boundary before execution. Models may propose a plan; only this
module may normalize it into an executable contract. Tool metadata, not model
claims, decides retryability and approval requirements.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Iterable, Optional, Dict, List, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class PlanContractError(ValueError):
    """A model proposal cannot safely enter the executor."""


class StepRuntime(StrEnum):
    SYNTHESIS = "synthesis"
    AGENT_COMPUTER = "agent_computer"
    BRIDGE = "bridge"
    CONNECTOR = "connector"
    MCP = "mcp"
    INTERNAL = "internal"


class IdempotencyClass(StrEnum):
    READ_ONLY = "read_only"
    IDEMPOTENT_WRITE = "idempotent_write"
    NON_IDEMPOTENT_WRITE = "non_idempotent_write"
    UNKNOWN = "unknown"


class FailureDisposition(StrEnum):
    RETRY = "retry"
    REPLAN = "replan"
    PAUSE = "pause"
    FAIL = "fail"


class FailureClass(StrEnum):
    TRANSIENT = "transient"
    AUTHENTICATION = "authentication"
    MISSING_DATA = "missing_data"
    WRONG_STATE = "wrong_state"
    ARGUMENT_ERROR = "argument_error"
    TOOL_UNAVAILABLE = "tool_unavailable"
    POSTCONDITION_FAILED = "postcondition_failed"
    USER_REQUIRED = "user_required"
    POLICY_BLOCKED = "policy_blocked"
    UNKNOWN = "unknown"


class VerificationType(StrEnum):
    TOOL_RESULT = "tool_result"
    FIELD_EXISTS = "field_exists"
    SCHEMA_VALIDATION = "schema_validation"
    ENTITY_EXISTS = "entity_exists"
    ENTITY_STATE = "entity_state"
    EQUALITY = "equality"
    CONTAINS = "contains"
    ABSENCE = "absence"
    BROWSER_URL = "browser_url"
    BROWSER_DOM = "browser_dom"
    BROWSER_TEXT = "browser_text"
    BROWSER_ATTRIBUTE = "browser_attribute"
    SOURCE_OF_TRUTH = "source_of_truth"
    BEFORE_AFTER_COMPARISON = "before_after_comparison"
    # The planner emits these two names. They are the same checks under
    # different labels, and plans already persisted in checkpoints carry the
    # labels below, so both spellings are accepted and normalised rather than
    # renamed. Anything the planner can emit must exist here, or Pydantic
    # rejects the postcondition and it is never verified.
    FIELD_EQUALS = "field_equals"
    BEFORE_AFTER_CHANGED = "before_after_changed"


class VerificationResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=1, ge=1, le=3)
    timeout_seconds: int = Field(default=60, ge=5, le=120)
    retry_on_timeout: bool = True


class VerificationRule(BaseModel):
    """Executable verification contract."""
    model_config = ConfigDict(extra="forbid")

    type: VerificationType = Field(..., description="Type of verification to perform")
    verifier_tool: Optional[str] = Field(None, description="Tool to call for verification")
    expected: Dict[str, Any] = Field(default_factory=dict, description="Expected state/value")
    entity_type: Optional[str] = Field(None, description="Type of entity to check")
    entity_id_ref: Optional[str] = Field(None, description="Reference to entity ID from step output")
    field: Optional[str] = Field(None, description="Field to check")
    value: Optional[Any] = Field(None, description="Value to check for")
    required: bool = Field(True, description="Whether this rule must pass")


class Postcondition(BaseModel):
    """Explicit condition that must be true after a step.

    ``required`` is emitted by the planner on most postconditions. It was
    missing from this model while ``extra="forbid"`` was set, so Pydantic
    rejected every postcondition carrying it and the verifier silently skipped
    it — which is most of them. Keep this field list in step with what
    ``Planner._generate_postconditions_for_step`` actually emits.
    """
    model_config = ConfigDict(extra="forbid")

    type: VerificationType
    description: str = Field(..., min_length=3, max_length=500)
    verifier_tool: Optional[str] = None
    expected: Dict[str, Any] = Field(default_factory=dict)
    field: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id_ref: Optional[str] = None
    required: bool = True


class StepOutputSchema(BaseModel):
    """Schema for what a step produces."""
    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., description="Type of output")
    fields: Dict[str, str] = Field(default_factory=dict, description="Field name -> type mapping")
    description: str = Field(default="", max_length=500)


class PlanStepContract(BaseModel):
    """Validated, JSON-serializable plan step consumed by the executor."""
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1, max_length=96)
    plan_revision_id: str | None = Field(default=None, max_length=96)
    resource_key: str | None = Field(default=None, max_length=240)
    concurrency_safe: bool = False
    depends_on: list[str] = Field(default_factory=list, max_length=24)
    runtime: StepRuntime
    tool_name: str | None = Field(default=None, max_length=160)
    tool_args: dict[str, Any] = Field(default_factory=dict)
    description: str = Field(min_length=3, max_length=1200)
    expected_outcome: str = Field(min_length=3, max_length=1200)
    verification: VerificationRule
    retry_policy: RetryPolicy
    approval_required: bool
    idempotency: IdempotencyClass
    on_failure: FailureDisposition
    postconditions: list[Postcondition] = Field(default_factory=list)
    output_schema: Optional[StepOutputSchema] = None
    observe: dict[str, Any] = Field(default_factory=dict)
    preconditions: list[str] = Field(default_factory=list, max_length=24)
    fallback: Optional[str] = Field(default=None, max_length=1000)
    session_required: bool = False

    @field_validator("step_id")
    @classmethod
    def clean_step_id(cls, value: str) -> str:
        candidate = value.strip()
        if not candidate or not all(char.isalnum() or char in "_-" for char in candidate):
            raise ValueError("step_id must contain only letters, digits, underscores, or hyphens")
        return candidate

    @model_validator(mode="after")
    def validate_runtime_and_tool(self) -> "PlanStepContract":
        if self.runtime == StepRuntime.SYNTHESIS:
            if self.tool_name is not None:
                raise ValueError("synthesis steps must not name a tool")
        elif not self.tool_name:
            raise ValueError("non-synthesis steps must name a tool")
        if self.step_id in self.depends_on:
            raise ValueError("a step cannot depend on itself")
        return self


class PlanContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[PlanStepContract] = Field(min_length=1, max_length=60)
    goal: str = Field(default="", description="Overall goal of the plan")
    success_conditions: list[VerificationRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_step_graph(self) -> "PlanContract":
        ids = [step.step_id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("plan step identifiers must be unique")
        known = set(ids)
        for step in self.steps:
            unknown = set(step.depends_on) - known
            if unknown:
                raise ValueError(f"step '{step.step_id}' depends on unknown step(s): {', '.join(sorted(unknown))}")
        graph = {step.step_id: set(step.depends_on) for step in self.steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visited:
                return
            if step_id in visiting:
                raise ValueError("plan dependencies contain a cycle")
            visiting.add(step_id)
            for dependency in graph[step_id]:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in graph:
            visit(step_id)
        return self


def runtime_for_tool(tool_name: str | None) -> StepRuntime:
    if not tool_name:
        return StepRuntime.SYNTHESIS
    if tool_name.startswith("bridge_"):
        return StepRuntime.BRIDGE
    if tool_name.startswith("computer_"):
        return StepRuntime.AGENT_COMPUTER
    if tool_name.startswith("mcp_"):
        return StepRuntime.MCP
    if tool_name.endswith("_tool") or tool_name in {"browser", "memory_retriever", "current_time", "python_repl"}:
        return StepRuntime.INTERNAL
    return StepRuntime.CONNECTOR


def safe_read_tool(tool_name: str | None, tool: Any | None) -> bool:
    if not tool_name or tool is None:
        return tool_name is None
    if bool(getattr(tool.schema, "irreversible", False)):
        return False
    name = tool_name.lower()
    safe_prefixes = ("read", "inspect", "list", "search", "find", "get", "browse", "current_time", "computer_navigate", "computer_scroll", "computer_back")
    return name.startswith(safe_prefixes) or any(token in name for token in ("read", "inspect", "search", "list", "retrieve", "navigate", "scroll"))


def tool_policy(tool_name: str | None, registry: Any) -> dict[str, Any]:
    """Return server-authoritative execution policy for a named tool."""
    if tool_name is None:
        return {
            "runtime": StepRuntime.SYNTHESIS,
            "idempotency": IdempotencyClass.READ_ONLY,
            "approval_required": False,
            "retry_policy": RetryPolicy(max_attempts=1, timeout_seconds=60),
            "on_failure": FailureDisposition.FAIL,
        }
    tool = registry.get(tool_name) if registry is not None else None
    if tool is None:
        raise PlanContractError(f"The selected tool '{tool_name}' is unavailable in this execution runtime.")
    schema = tool.schema
    declared_runtime = getattr(schema, "runtime", None)
    runtime = StepRuntime(declared_runtime) if declared_runtime else runtime_for_tool(tool_name)
    declared_idempotency = getattr(schema, "idempotency", None)
    if declared_idempotency:
        idempotency = IdempotencyClass(declared_idempotency)
    elif safe_read_tool(tool_name, tool):
        idempotency = IdempotencyClass.READ_ONLY
    else:
        idempotency = IdempotencyClass.NON_IDEMPOTENT_WRITE if bool(getattr(schema, "irreversible", False)) else IdempotencyClass.UNKNOWN
    declared_approval = getattr(schema, "approval_required", None)
    approval_required = bool(getattr(schema, "irreversible", False)) if declared_approval is None else bool(declared_approval)
    timeout_seconds = int(getattr(schema, "timeout_seconds", 60) or 60)
    timeout_seconds = max(5, min(timeout_seconds, 120))
    if idempotency == IdempotencyClass.READ_ONLY:
        max_attempts = max(1, min(int(getattr(schema, "max_attempts", 2) or 2), 3))
        on_failure = FailureDisposition.REPLAN
    elif idempotency == IdempotencyClass.IDEMPOTENT_WRITE:
        max_attempts = 1
        on_failure = FailureDisposition.PAUSE if approval_required else FailureDisposition.FAIL
    else:
        max_attempts = 1
        on_failure = FailureDisposition.PAUSE if approval_required else FailureDisposition.FAIL
    return {
        "runtime": runtime,
        "idempotency": idempotency,
        "approval_required": approval_required,
        "retry_policy": RetryPolicy(max_attempts=max_attempts, timeout_seconds=timeout_seconds),
        "on_failure": on_failure,
    }


def normalize_plan(raw_plan: Any, registry: Any) -> list[dict[str, Any]]:
    """Validate and enrich a model-proposed plan using authoritative policy.

    Existing prompts and deterministic plans remain compatible: omitted fields are
    filled from the registered tool contract. Invalid plans fail before entering
    the executor and can therefore consume a bounded replan attempt instead of
    creating a tool/replan loop.
    """
    if not isinstance(raw_plan, list) or not raw_plan:
        raise PlanContractError("The planner did not return a non-empty list of steps.")
    normalized: list[dict[str, Any]] = []
    prior_step_id: str | None = None
    for index, candidate in enumerate(raw_plan):
        if not isinstance(candidate, dict):
            raise PlanContractError(f"Plan step {index + 1} is not an object.")
        tool_name = candidate.get("tool_name")
        if tool_name is not None:
            tool_name = str(tool_name).strip() or None
        policy = tool_policy(tool_name, registry)
        step_id = str(candidate.get("step_id") or f"step_{index + 1}")
        raw_dependencies = candidate.get("depends_on")
        if raw_dependencies is None:
            dependencies = [prior_step_id] if prior_step_id else []
        elif isinstance(raw_dependencies, list):
            dependencies = [str(value) for value in raw_dependencies]
        else:
            raise PlanContractError(f"Step '{step_id}' has non-list dependencies.")
        raw_retry = candidate.get("retry_policy") or {}
        requested_attempts = raw_retry.get("max_attempts", raw_retry.get("attempts", policy["retry_policy"].max_attempts)) if isinstance(raw_retry, dict) else policy["retry_policy"].max_attempts
        requested_timeout = raw_retry.get("timeout_seconds", policy["retry_policy"].timeout_seconds) if isinstance(raw_retry, dict) else policy["retry_policy"].timeout_seconds
        # A model can only reduce the authoritative retry budget, never expand a
        # non-idempotent tool into an automatically repeated action.
        retry_policy = policy["retry_policy"].model_copy(update={
            "max_attempts": max(1, min(int(requested_attempts or 1), policy["retry_policy"].max_attempts)),
            "timeout_seconds": max(5, min(int(requested_timeout or policy["retry_policy"].timeout_seconds), policy["retry_policy"].timeout_seconds)),
        })
        
        # Parse postconditions from candidate
        postconditions = []
        raw_postconditions = candidate.get("postconditions", [])
        if isinstance(raw_postconditions, list):
            for raw_pc in raw_postconditions:
                if isinstance(raw_pc, dict):
                    try:
                        pc_type = raw_pc.get("type")
                        if pc_type:
                            postconditions.append(Postcondition(
                                type=VerificationType(pc_type),
                                description=str(raw_pc.get("description", "")),
                                verifier_tool=raw_pc.get("verifier_tool"),
                                expected=raw_pc.get("expected", {}),
                                field=raw_pc.get("field"),
                                entity_type=raw_pc.get("entity_type"),
                                entity_id_ref=raw_pc.get("entity_id_ref"),
                            ))
                    except (ValueError, TypeError):
                        pass
        
        # Parse output schema
        output_schema = None
        raw_schema = candidate.get("output_schema")
        if isinstance(raw_schema, dict):
            try:
                output_schema = StepOutputSchema(
                    type=str(raw_schema.get("type", "object")),
                    fields=raw_schema.get("fields", {}),
                    description=str(raw_schema.get("description", "")),
                )
            except (ValueError, TypeError):
                pass
        
        raw_verification = candidate.get("verification")
        if isinstance(raw_verification, dict):
            verification_criterion = str(
                raw_verification.get("criterion")
                or raw_verification.get("description")
                or candidate.get("expected_outcome")
                or "A structured result is returned for verification."
            )
            verification_type = raw_verification.get("type", VerificationType.TOOL_RESULT.value)
            verification_expected = raw_verification.get("expected", {})
            if not isinstance(verification_expected, dict):
                verification_expected = {}
        else:
            verification_criterion = str(
                candidate.get("expected_outcome")
                or "A structured result is returned for verification."
            )
            verification_type = VerificationType.TOOL_RESULT.value
            verification_expected = {}
        if verification_type not in {item.value for item in VerificationType}:
            verification_type = VerificationType.TOOL_RESULT.value
        verification_expected.setdefault("criterion", verification_criterion)

        normalized_step = {
            "step_id": step_id,
            "depends_on": dependencies,
            "runtime": policy["runtime"].value,
            "tool_name": tool_name,
            "tool_args": candidate.get("tool_args") if isinstance(candidate.get("tool_args"), dict) else {},
            "description": str(candidate.get("description") or "Complete the requested work."),
            "expected_outcome": str(candidate.get("expected_outcome") or candidate.get("expected_result") or "A structured result is returned for verification."),
            "verification": {
                "type": verification_type,
                "expected": verification_expected,
                "required": True,
            },
            "retry_policy": retry_policy.model_dump(),
            "approval_required": bool(policy["approval_required"]),
            "idempotency": policy["idempotency"].value,
            "on_failure": policy["on_failure"].value,
            "postconditions": [pc.model_dump() for pc in postconditions],
            "output_schema": output_schema.model_dump() if output_schema else None,
            "observe": candidate.get("observe") if isinstance(candidate.get("observe"), dict) else {},
            "preconditions": [str(value) for value in candidate.get("preconditions", [])] if isinstance(candidate.get("preconditions"), list) else [],
            "fallback": str(candidate.get("fallback"))[:1000] if candidate.get("fallback") else None,
            "session_required": bool(tool_name and str(tool_name).startswith("bridge_")),
        }
        normalized.append(normalized_step)
        prior_step_id = step_id
    try:
        contract = PlanContract(steps=normalized)
    except ValidationError as exc:
        raise PlanContractError(str(exc)) from exc
    return [step.model_dump() for step in contract.steps]


def validated_plan(plan: Any, registry: Any | None = None) -> bool:
    """Validate persisted plans. Registry is optional for graph-only checks."""
    try:
        if registry is not None:
            normalize_plan(plan, registry)
        else:
            PlanContract(steps=plan)
        return True
    except (PlanContractError, ValidationError, TypeError, ValueError):
        return False


def terminal_failure_message(step: dict[str, Any], failure: str) -> str:
    return f"Synth stopped at '{step.get('description', 'the current step')}' because {failure}. The completed evidence has been retained."


__all__ = [
    "PlanContractError",
    "StepRuntime",
    "IdempotencyClass",
    "FailureDisposition",
    "FailureClass",
    "VerificationType",
    "VerificationResult",
    "RetryPolicy",
    "VerificationRule",
    "Postcondition",
    "StepOutputSchema",
    "PlanStepContract",
    "PlanContract",
    "runtime_for_tool",
    "safe_read_tool",
    "tool_policy",
    "normalize_plan",
    "validated_plan",
    "terminal_failure_message",
]