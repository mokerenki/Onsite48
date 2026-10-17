"""Phase 2 adoption: hierarchical plan awareness inside loop.run.

Additive, env-gated (ENABLE_HIERARCHICAL_PLANS), and fail-safe: any error
anywhere returns the loop to exactly its previous flat behavior. The plan is an
awareness + progress-tracking layer injected into the model context — step
execution stays model-driven. Plans persist via journal events so checkpoint
resume rebuilds them.
"""
from __future__ import annotations

import json
import os
from typing import Any

import structlog

logger = structlog.get_logger("onsite48.plan_driver")

ENABLE_HIERARCHICAL_PLANS = os.environ.get("ENABLE_HIERARCHICAL_PLANS", "").lower() in ("1", "true", "yes")
MAX_PLAN_NODES = int(os.environ.get("MAX_PLAN_NODES", "50"))
MAX_REPLANS_PER_RUN = int(os.environ.get("MAX_REPLANS_PER_RUN", "5"))

_plans: dict[str, "HierarchicalPlan"] = {}
_replan_counts: dict[str, int] = {}

_PLAN_PROMPT = """Create a concise execution plan for the goal as a JSON object:
{{"refined_goal": "<goal>", "sub_goals": [{{"id": "sg1", "description": "...",
"steps": [{{"step_id": "s1", "description": "...", "tool_name": "<tool or null>",
"depends_on": []}}], "success_condition": "..."}}], "success_conditions": ["..."]}}
Rules: at most 3 sub-goals, at most 8 steps per sub-goal, step_ids unique,
depends_on only references earlier step ids in the same sub-goal. Available
tool names: {tools}. Goal: {goal}"""


def _get_plan(run_id: str) -> "HierarchicalPlan | None":
    return _plans.get(run_id)


async def _restore_from_journal(run_id: str, journal: Any) -> "HierarchicalPlan | None":
    try:
        events = await journal.list_events(run_id)
    except Exception:
        return None
    last_plan = None
    for ev in events:
        if (ev.get("type") or ev.get("event_type")) in ("plan_created", "plan_revised"):
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            if isinstance(payload.get("plan"), dict):
                last_plan = payload["plan"]
    if last_plan is None:
        return None
    from app.runtime.hierarchical_plan import HierarchicalPlan
    plan = HierarchicalPlan.from_dict(last_plan)
    _plans[run_id] = plan
    return plan


async def maybe_create_plan(*, run_id: str, goal: str, specs: dict, journal: Any,
                            event_emitter: Any = None, model_override: str | None = None,
                            model_router: Any = None) -> bool:
    """Create a plan at run start. Returns True when a plan is active."""
    if not ENABLE_HIERARCHICAL_PLANS:
        return False
    try:
        existing = _get_plan(run_id) or await _restore_from_journal(run_id, journal)
        if existing is not None:
            return True
        from app.core.structured_plans import parse_and_validate_plan_payload
        from app.runtime.hierarchical_plan import HierarchicalPlan
        from app.runtime.models import complete as model_complete

        tool_names = sorted({n for n, s in specs.items() if getattr(s, "_execute", None) is not None})[:30]
        prompt = _PLAN_PROMPT.format(goal=(goal or "")[:1500], tools=", ".join(tool_names))
        result = await model_complete(
            [{"role": "user", "content": prompt}],
            tools=[], role="planner", tool_choice="none",
            max_tokens=1800, model_override=model_override, model_router=model_router,
            goal=goal,
        )
        payload = parse_and_validate_plan_payload(result.content or "")
        plan = HierarchicalPlan.from_payload(payload)
        step_count = sum(1 for n in plan.nodes.values() if n.kind == "step")
        if step_count == 0 or step_count > MAX_PLAN_NODES:
            logger.info("plan_driver.plan_rejected", run_id=run_id, steps=step_count)
            return False
        _plans[run_id] = plan
        await journal.append(run_id, "plan_created", {"plan": plan.to_dict(), "step_count": step_count})
        from app.runtime.loop import _emit_live_event
        await _emit_live_event(event_emitter, {
            "type": "plan_created",
            "content": {"goal": plan.goal, "sub_goals": [n.id for n in plan.nodes.values() if n.kind == "subgoal"],
                        "steps": step_count},
            "transient": True,
        })
        return True
    except Exception as exc:
        logger.warning("plan_driver.create_failed", run_id=run_id, error=str(exc)[:200])
        return False


def plan_context_message(run_id: str) -> dict | None:
    """System message injected ahead of each model call; None when done/absent."""
    plan = _get_plan(run_id)
    if plan is None or plan.is_complete():
        return None
    return {"role": "system", "content": "[Hierarchical plan]\n" + plan.to_context_block()}


async def advance_plan(*, run_id: str, tool_name: str, ok: bool, journal: Any,
                       event_emitter: Any = None) -> None:
    """Mark the running node(s) matching this tool call, emit progress."""
    plan = _get_plan(run_id)
    if plan is None:
        return
    try:
        matched = [n for n in plan.nodes.values()
                   if n.kind == "step" and n.status == "running" and n.tool_name == tool_name]
        for node in matched:
            if ok:
                plan.mark_done(node.id, notes=f"via {tool_name}")
            else:
                plan.mark_failed(node.id, notes=f"{tool_name} failed")
        if matched:
            await journal.append(run_id, "plan_progress", {"plan": plan.to_dict()})
            from app.runtime.loop import _emit_live_event
            await _emit_live_event(event_emitter, {
                "type": "plan_progress", "content": plan.progress(), "transient": True,
            })
    except Exception as exc:
        logger.debug("plan_driver.advance_failed", run_id=run_id, error=str(exc)[:150])


async def mark_step_running(*, run_id: str, tool_name: str) -> None:
    """Before executing a tool, flag the first ready node that names it."""
    plan = _get_plan(run_id)
    if plan is None:
        return
    try:
        for node in plan.ready_steps():
            if node.tool_name == tool_name:
                plan.mark_running(node.id)
                return
    except Exception:
        pass


async def replan_after_verifier_reject(*, run_id: str, goal: str, unmet: list,
                                       specs: dict, journal: Any, event_emitter: Any = None,
                                       model_override: str | None = None,
                                       model_router: Any = None) -> None:
    """Bounded replan (MAX_REPLANS_PER_RUN) after the verifier rejects finish."""
    if not ENABLE_HIERARCHICAL_PLANS:
        return
    if _replan_counts.get(run_id, 0) >= MAX_REPLANS_PER_RUN:
        return
    try:
        from app.core.structured_plans import parse_and_validate_plan_payload
        from app.runtime.hierarchical_plan import HierarchicalPlan
        from app.runtime.models import complete as model_complete
        _replan_counts[run_id] = _replan_counts.get(run_id, 0) + 1
        current = _get_plan(run_id)
        current_block = current.to_context_block() if current else "(no plan)"
        prompt = (
            f"The verifier rejected the finish. Unmet requirements: {json.dumps(unmet)[:1500]}\n"
            f"Current plan:\n{current_block[:2500]}\n\n"
            "Revise ONLY what is needed as the same JSON plan format "
            "(refined_goal, sub_goals[].steps[] with unique step_ids)."
        )
        result = await model_complete(
            [{"role": "user", "content": prompt}],
            tools=[], role="planner", tool_choice="none",
            max_tokens=1800, model_override=model_override, model_router=model_router,
            goal=goal,
        )
        payload = parse_and_validate_plan_payload(result.content or "")
        new_plan = HierarchicalPlan.from_payload(payload)
        new_plan.version = (current.version if current else 1) + 1
        _plans[run_id] = new_plan
        await journal.append(run_id, "plan_revised", {"plan": new_plan.to_dict(), "reason": "verifier_reject"})
        from app.runtime.loop import _emit_live_event
        await _emit_live_event(event_emitter, {
            "type": "plan_revised",
            "content": {"version": new_plan.version, "unmet": [str(u)[:120] for u in unmet[:5]]},
            "transient": True,
        })
    except Exception as exc:
        logger.warning("plan_driver.replan_failed", run_id=run_id, error=str(exc)[:200])
