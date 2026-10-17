"""Workstream D — loop-contract offline eval tasks (no LLM / no network)."""
from __future__ import annotations

from typing import Any, Callable

from evals.flywheel import EvalTask


def _check_plan_bootstrap(_: dict[str, Any]) -> bool:
    from app.runtime.plan_integration import bootstrap_plan

    p = bootstrap_plan("Research competitor pricing and compare primary sources", research_depth="standard")
    return p is not None and "nodes" in p


def _check_plan_finish_gate(_: dict[str, Any]) -> bool:
    from app.runtime.plan_integration import bootstrap_plan, finish_should_retry_for_plan

    p = bootstrap_plan("Build a multi-step research report with citations", research_depth="deep")
    retry, open_steps = finish_should_retry_for_plan(p, finish_attempts=1)
    return retry and len(open_steps) >= 1


def _check_plan_advance(_: dict[str, Any]) -> bool:
    from app.runtime.plan_integration import advance_plan_on_tool, bootstrap_plan

    p = bootstrap_plan("Implement a Python fix and run tests")
    p2 = advance_plan_on_tool(p, tool_name="python_exec", ok=True, result_summary="all tests passed")
    steps = [n for n in (p2 or {}).get("nodes", {}).values() if n.get("kind") == "step"]
    return any(s.get("status") == "done" for s in steps)


def _check_post_run(_: dict[str, Any]) -> bool:
    import tempfile
    from pathlib import Path

    from app.runtime.post_run import finalize_run

    with tempfile.TemporaryDirectory() as td:
        Path(td, "a.txt").write_text("x", encoding="utf-8")
        out = finalize_run(
            run_id="eval-loop",
            goal="always prefer primary sources when researching markets",
            finish_summary="- Market share for Acme is documented at 12 percent in the annual report.",
            workspace_root=td,
        )
        return bool(out.get("memory")) and bool(out.get("artifacts"))


def _check_strategy_in_module(_: dict[str, Any]) -> bool:
    from app.runtime.model_strategy import TaskSignals, select_strategy

    d = select_strategy(TaskSignals(is_verification=True))
    return d.router_role in ("verification", "verifier") or d.lane == "verifier"


LOOP_EVAL_TASKS: list[EvalTask] = [
    EvalTask("loop-plan-001", "Bootstrap hierarchical plan for research goal", "quick", _check_plan_bootstrap, ["plan"]),
    EvalTask("loop-plan-002", "Finish gate rejects open critical steps", "quick", _check_plan_finish_gate, ["plan"]),
    EvalTask("loop-plan-003", "Tool success advances a ready plan step", "quick", _check_plan_advance, ["plan"]),
    EvalTask("loop-postrun-001", "Post-run produces memory and artifacts", "standard", _check_post_run, ["memory", "artifacts"]),
    EvalTask("loop-strategy-001", "Strategy maps verifier signals to verifier lane", "quick", _check_strategy_in_module, ["model"]),
]
