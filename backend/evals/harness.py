"""Offline trajectory runner: real planner, real registry, fake routers.

The harness never touches the network. It builds a real :class:`ToolRegistry`
from the production connector tools plus the deterministic local tools, wires a
real :class:`~app.agents.planner.Planner` with the same fake routers the
existing unit tests use, and records one :class:`Trajectory` per corpus case.

The model planning path is evaluated offline through
:class:`ScriptedModelRouter`, which replays a canned, schema-shaped plan for a
goal. That is the point of the harness: it can judge plans the *model* would
produce, not only the regex shortcuts.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from app.tools.browser_tool import BrowserTool
from app.tools.connector_tools import (
    GitHubCreateIssueTool,
    GitHubGetFileTool,
    GitHubGetRepositoryTool,
    GitHubListIssuesTool,
    GitHubListRepositoriesTool,
    GmailCountDraftsTool,
    GmailCreateDraftTool,
    GmailDeleteDraftTool,
    GmailDeleteMessageTool,
    GmailListDraftsTool,
    GmailSearchMessagesTool,
    GmailSendMessageTool,
    GmailTrashMessageTool,
    GoogleCalendarCreateEventTool,
    GoogleCalendarDeleteEventTool,
    GoogleCalendarListEventsTool,
    GoogleCalendarUpdateEventTool,
    SlackListChannelsTool,
    SlackSendMessageTool,
)
from app.tools.current_time import CurrentTimeTool
from app.tools.document_tool import DocumentTool
from app.tools.registry import ToolRegistry
from app.tools.wide_research_tool import WideResearchTool

from evals.invariants import InvariantResult, run_invariant
from evals.trajectories import InvariantSpec, TrajectoryCase

EVAL_USER_ID = "trajectory-eval-user"


class ScriptedModelRouter:
    """Offline stand-in for the production model router.

    ``responses`` maps a routing role to either a raw string (already a
    schema-shaped planner payload) or a callable taking the message list. The
    payload is handed back verbatim so ``extract_plan_steps`` parses exactly
    what a real provider would have returned.
    """

    def __init__(self, responses: Mapping[str, Any] | None = None) -> None:
        self.responses: Dict[str, Any] = dict(responses or {})
        self.calls: List[Dict[str, Any]] = []

    async def route(
        self,
        role: str,
        messages: Any = None,
        **_kwargs: Any,
    ) -> Any:
        self.calls.append({"role": role, "messages": messages})
        if role not in self.responses:
            raise AssertionError(f"ScriptedModelRouter has no canned response for role '{role}'.")
        payload = self.responses[role]
        if callable(payload):
            payload = payload(messages)
        content = payload if isinstance(payload, str) else json.dumps(payload)
        return SimpleNamespace(content=content, provider="scripted", model="scripted-planner", usage=None)


class StaticDomainRouter:
    """Domain router that always returns the general planning lane."""

    def __init__(self, domain: str = "general") -> None:
        self.domain = domain
        self.calls: List[Any] = []

    async def route(self, *_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        self.calls.append(_args)
        return {
            "domain": self.domain,
            "context": {},
            "system_prompt": "Plan safely and never invent a tool.",
        }


def build_registry() -> ToolRegistry:
    """A real registry holding the production tools the planner can select."""
    registry = ToolRegistry()
    for tool in (
        BrowserTool(),
        CurrentTimeTool(),
        DocumentTool(),
        GmailSearchMessagesTool(),
        GmailListDraftsTool(),
        GmailCountDraftsTool(),
        GmailCreateDraftTool(),
        GmailSendMessageTool(),
        GmailDeleteDraftTool(),
        GmailDeleteMessageTool(),
        GmailTrashMessageTool(),
        GoogleCalendarListEventsTool(),
        GoogleCalendarCreateEventTool(),
        GoogleCalendarUpdateEventTool(),
        GoogleCalendarDeleteEventTool(),
        GitHubListRepositoriesTool(),
        GitHubListIssuesTool(),
        GitHubGetRepositoryTool(),
        GitHubGetFileTool(),
        GitHubCreateIssueTool(),
        SlackListChannelsTool(),
        SlackSendMessageTool(),
    ):
        registry.register(tool)
    registry.register(WideResearchTool(browser=BrowserTool(), router=ScriptedModelRouter()))
    return registry


@dataclass
class Trajectory:
    """One recorded run of a goal through the planner."""

    case_id: str
    family: str
    goal: str
    tags: Sequence[str] = field(default_factory=tuple)
    intent: Dict[str, Any] = field(default_factory=dict)
    plan: List[Dict[str, Any]] = field(default_factory=list)
    planner_path: str = "deterministic"
    plan_timing_ms: float = 0.0
    facts: Dict[str, Any] = field(default_factory=dict)
    registry: Optional[ToolRegistry] = None
    case: Optional[TrajectoryCase] = None
    error: Optional[str] = None

    @property
    def tool_names(self) -> List[str]:
        return [str(step.get("tool_name") or "") for step in self.plan if step.get("tool_name")]

    @property
    def passed(self) -> bool:
        return bool(self.plan) and self.error is None and all(r.passed for r in self.results)

    def signature(self) -> Dict[str, Any]:
        """The deterministic, timing-free shape used for snapshot diffing."""
        return {
            "id": self.case_id,
            "family": self.family,
            "goal": self.goal,
            "intent": {
                "resource": str(self.intent.get("resource") or ""),
                "operation": str(self.intent.get("operation") or ""),
            },
            "planner_path": self.planner_path,
            "step_count": len(self.plan),
            "tools": self.tool_names,
            "step_ids": [str(step.get("step_id") or "") for step in self.plan],
            "results": {result.name: result.passed for result in self.results},
        }

    def to_jsonl(self) -> Dict[str, Any]:
        payload = self.signature()
        payload["tags"] = list(self.tags)
        payload["plan_timing_ms"] = round(self.plan_timing_ms, 3)
        payload["plan"] = [
            {
                "step_id": str(step.get("step_id") or ""),
                "tool_name": step.get("tool_name"),
                "tool_args": step.get("tool_args") or {},
                "depends_on": list(step.get("depends_on") or []),
            }
            for step in self.plan
        ]
        payload["details"] = {result.name: result.detail for result in self.results}
        if self.error:
            payload["error"] = self.error
        return payload

    @property
    def results(self) -> List[InvariantResult]:
        specs = list(self.case.invariants) if self.case is not None else []
        return [run_invariant(spec.name, self, spec.params) for spec in specs]


def _scripted_content(case: TrajectoryCase) -> Any:
    if case.scripted_plan is None:
        return None
    return json.dumps({"plan": list(case.scripted_plan)})


def build_planner(case: TrajectoryCase, registry: ToolRegistry) -> tuple[Planner, ScriptedModelRouter]:
    """Construct the real planner with the same fake routers existing tests use."""
    responses: Dict[str, Any] = {}
    scripted = _scripted_content(case)
    if scripted is not None:
        responses["planning"] = scripted
    if case.scripted_responses:
        responses.update(case.scripted_responses)
    model_router = ScriptedModelRouter(responses)
    planner = None  # Phase3: Planner removed; (model_router, StaticDomainRouter(), verifier=None, tool_registry=registry)
    return planner, model_router


def record_plan(
    case: TrajectoryCase,
    plan: Sequence[Mapping[str, Any]],
    *,
    planner_path: str,
    registry: Optional[ToolRegistry] = None,
    intent: Optional[Mapping[str, Any]] = None,
    plan_timing_ms: float = 0.0,
    error: Optional[str] = None,
) -> Trajectory:
    """Build a trajectory from an already-resolved plan (no planner call).

    Used to evaluate a plan the model produced offline, and to prove the
    invariants can fail on a deliberately bad plan.
    """
    resolved_intent = dict(intent) if intent is not None else resolve_intent(case.goal).model_dump(mode="json")
    return Trajectory(
        case_id=case.id,
        family=case.family,
        goal=case.goal,
        tags=case.tags,
        intent=resolved_intent,
        plan=[dict(step) for step in plan],
        planner_path=planner_path,
        plan_timing_ms=plan_timing_ms,
        facts={
            "scripted": case.scripted_plan is not None,
            "expected_tools": list(case.expected_tools),
            "forbidden_tools": list(case.forbidden_tools),
        },
        registry=registry if registry is not None else build_registry(),
        case=case,
        error=error,
    )


async def run_case(case: TrajectoryCase, registry: Optional[ToolRegistry] = None) -> Trajectory:
    """Run one corpus case through the real planner and record the trajectory."""
    active_registry = registry if registry is not None else build_registry()
    planner, model_router = build_planner(case, active_registry)
    intent = resolve_intent(case.goal).model_dump(mode="json")
    state = dict(case.state or {})
    calls_before = len(model_router.calls)
    error: Optional[str] = None
    plan: List[Dict[str, Any]] = []
    started = time.perf_counter()
    try:
        plan = await planner.generate_plan(
            case.goal,
            context=case.context,
            user_id=EVAL_USER_ID,
            thread_id=f"trajectory-eval-{case.id}",
            state=state or None,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    timing_ms = (time.perf_counter() - started) * 1000.0

    model_called = len(model_router.calls) > calls_before
    path = "model" if model_called else "deterministic"
    if not plan:
        path = "fallback"
    facts = {
        "model_called": model_called,
        "scripted": case.scripted_plan is not None,
        "expected_tools": list(case.expected_tools),
        "forbidden_tools": list(case.forbidden_tools),
        "input_state_keys": sorted(state.keys()),
    }
    return Trajectory(
        case_id=case.id,
        family=case.family,
        goal=case.goal,
        tags=case.tags,
        intent=intent,
        plan=[dict(step) for step in plan or []],
        planner_path=path,
        plan_timing_ms=timing_ms,
        facts=facts,
        registry=active_registry,
        case=case,
        error=error,
    )


def run_sync(cases: Iterable[TrajectoryCase]) -> List[Trajectory]:
    """Run a whole corpus synchronously, sharing one registry."""
    registry = build_registry()
    return asyncio.run(_run_all(cases, registry))


async def _run_all(cases: Iterable[TrajectoryCase], registry: ToolRegistry) -> List[Trajectory]:
    return [await run_case(case, registry) for case in cases]


def evaluate(
    case: TrajectoryCase,
    plan: Sequence[Mapping[str, Any]],
    *,
    planner_path: str = "model",
    registry: Optional[ToolRegistry] = None,
) -> Trajectory:
    """Convenience wrapper around :func:`record_plan` for offline plan review."""
    return record_plan(case, plan, planner_path=planner_path, registry=registry)