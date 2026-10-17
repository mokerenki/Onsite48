"""Loop integration for hierarchical plans (Workstream A).

Pure helpers — loop.py only calls these; no circular imports.
"""
from __future__ import annotations

import os
import re
from typing import Any

from app.runtime.hierarchical_plan import HierarchicalPlan


def plan_integration_enabled() -> bool:
    return os.environ.get("ENABLE_HIERARCHICAL_PLAN", "1").lower() not in ("0", "false", "no", "off")


def should_bootstrap_plan(
    goal: str,
    *,
    research_depth: str | None = None,
    is_fast_lane: bool = False,
) -> bool:
    if not plan_integration_enabled():
        return False
    if is_fast_lane:
        return False
    g = (goal or "").strip()
    if len(g) < 12:
        return False
    if research_depth in ("standard", "deep"):
        return True
    low = g.lower()
    multi_signals = (
        " and ",
        " then ",
        "research",
        "compare",
        "build",
        "implement",
        "browser",
        "website",
        "spreadsheet",
        "report",
        "steps",
        "plan",
    )
    if any(s in low for s in multi_signals):
        return True
    # Long goals are usually multi-step
    return len(g) >= 80


def _default_steps_for_goal(goal: str) -> list[dict[str, Any]]:
    low = (goal or "").lower()
    if any(k in low for k in ("browser", "website", "click", "login", "form")):
        steps = [
            {"step_id": "s1", "description": "Observe the target page and identify controls"},
            {"step_id": "s2", "description": "Perform the required browser actions", "depends_on": ["s1"]},
            {"step_id": "s3", "description": "Confirm final page state with a fresh observation", "depends_on": ["s2"]},
        ]
    elif any(k in low for k in ("code", "implement", "debug", "refactor", "python", "repo")):
        steps = [
            {"step_id": "s1", "description": "Inspect relevant files and reproduce the issue"},
            {"step_id": "s2", "description": "Apply code changes", "depends_on": ["s1"]},
            {"step_id": "s3", "description": "Run tests or verification", "depends_on": ["s2"]},
        ]
    elif any(k in low for k in ("research", "compare", "find", "sources", "market")):
        steps = [
            {"step_id": "s1", "description": "Search and open primary sources"},
            {"step_id": "s2", "description": "Extract and cross-check load-bearing facts", "depends_on": ["s1"]},
            {"step_id": "s3", "description": "Synthesize answer with citations", "depends_on": ["s2"]},
        ]
    else:
        steps = [
            {"step_id": "s1", "description": "Clarify requirements and gather inputs"},
            {"step_id": "s2", "description": "Execute the main work", "depends_on": ["s1"]},
            {"step_id": "s3", "description": "Verify outcome against the goal", "depends_on": ["s2"]},
        ]
    return steps


def bootstrap_plan(
    goal: str,
    *,
    research_depth: str | None = None,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return plan dict or None if bootstrap skipped."""
    if existing:
        try:
            return HierarchicalPlan.from_payload(existing).to_dict()
        except Exception:
            pass
    if not should_bootstrap_plan(goal, research_depth=research_depth):
        return None
    plan = HierarchicalPlan.from_flat_steps(goal, _default_steps_for_goal(goal))
    return plan.to_dict()


def load_plan(payload: dict[str, Any] | None) -> HierarchicalPlan | None:
    if not payload:
        return None
    try:
        return HierarchicalPlan.from_payload(payload)
    except Exception:
        return None


def advance_plan_on_tool(
    plan_payload: dict[str, Any] | None,
    *,
    tool_name: str,
    ok: bool,
    result_summary: str = "",
) -> dict[str, Any] | None:
    """Conservatively mark one ready step done/failed based on tool success."""
    plan = load_plan(plan_payload)
    if plan is None:
        return plan_payload
    ready = plan.ready_steps()
    if not ready:
        return plan.to_dict()

    tool = (tool_name or "").lower()
    summary = (result_summary or "").lower()
    # Prefer step whose description overlaps tool or summary tokens
    tool_tokens = set(re.findall(r"[a-z0-9]{3,}", tool))
    summary_tokens = set(re.findall(r"[a-z0-9]{3,}", summary))

    best = ready[0]
    best_score = -1
    for step in ready:
        desc_tokens = set(re.findall(r"[a-z0-9]{3,}", (step.description or "").lower()))
        score = len(desc_tokens & tool_tokens) * 2 + len(desc_tokens & summary_tokens)
        if step.tool_name and step.tool_name.lower() == tool:
            score += 5
        if score > best_score:
            best_score = score
            best = step

    if ok:
        plan.mark_done(best.id, notes=f"auto:{tool_name}")
    else:
        plan.mark_failed(best.id, notes=f"auto_fail:{tool_name}")
    return plan.to_dict()


def open_critical_steps(plan_payload: dict[str, Any] | None) -> list[dict[str, str]]:
    plan = load_plan(plan_payload)
    if plan is None:
        return []
    open_steps = []
    for n in plan.nodes.values():
        if n.kind != "step":
            continue
        if n.status in ("pending", "ready", "running"):
            open_steps.append({"id": n.id, "status": n.status, "description": n.description})
    return open_steps


def finish_should_retry_for_plan(
    plan_payload: dict[str, Any] | None,
    *,
    finish_attempts: int,
    max_attempts: int = 2,
) -> tuple[bool, list[dict[str, str]]]:
    """True if finish should be rejected once due to open plan steps."""
    if not plan_integration_enabled():
        return False, []
    open_steps = open_critical_steps(plan_payload)
    if not open_steps:
        return False, []
    if finish_attempts >= max_attempts:
        return False, open_steps
    return True, open_steps


__all__ = [
    "plan_integration_enabled",
    "should_bootstrap_plan",
    "bootstrap_plan",
    "load_plan",
    "advance_plan_on_tool",
    "open_critical_steps",
    "finish_should_retry_for_plan",
]
