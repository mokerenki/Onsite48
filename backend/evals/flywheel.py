"""Phase 10 — Eval flywheel.

Lightweight offline tasks that exercise the runtime modules without HTTP.
Each task: {id, prompt, depth, check(result)->bool, tags}.
Runner collects JSONL rows: pass, failure_tag, duration_s, cost_usd, steps.

Taxonomy (fixed):
  plan_error / tool_error / observation_miss / context_overflow /
  verifier_miss / infra
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

FAILURE_TAGS = (
    "plan_error",
    "tool_error",
    "observation_miss",
    "context_overflow",
    "verifier_miss",
    "infra",
)


@dataclass
class EvalTask:
    id: str
    prompt: str
    depth: str  # quick | standard | deep
    check: Callable[[dict[str, Any]], bool]
    tags: list[str] = field(default_factory=list)


def _check_observation_ok(result: dict[str, Any]) -> bool:
    from app.runtime.observation import observe_tool_result

    obs = observe_tool_result(tool="web_search", call_id="t1", result=result.get("tool_result") or {"ok": True})
    return obs.outcome == "ok" and bool(obs.fingerprint)


def _check_plan_dag(result: dict[str, Any]) -> bool:
    from app.runtime.hierarchical_plan import HierarchicalPlan

    plan = HierarchicalPlan.from_payload(result.get("plan_payload") or {
        "goal": "test",
        "plan": [
            {"step_id": "s1", "description": "A"},
            {"step_id": "s2", "description": "B", "depends_on": ["s1"]},
        ],
    })
    return len(plan.ready_steps()) == 1 and plan.ready_steps()[0].id == "s1"


def _check_context_budget(result: dict[str, Any]) -> bool:
    from app.runtime.context_engine import ContextEngine

    engine = ContextEngine(budget_tokens=500, recent_messages=3)
    history = [{"role": "user", "content": ("x" * 200)} for _ in range(20)]
    pack = engine.pack(system="sys", goal="g", history=history)
    return pack.tokens_estimated <= engine.budget_tokens + 80 and pack.compacted


def _check_claim_grounding(result: dict[str, Any]) -> bool:
    from app.runtime.claim_grounding import ground_claims

    report = ground_claims(
        result.get("summary") or "Acme pricing is 49 dollars per month on the pricing page documented fully.",
        journal_events=result.get("events") or [{
            "type": "tool_call_result",
            "payload": {"result": {"snippet": "Acme pricing is 49 dollars per month on the pricing page documented fully"}},
        }],
    )
    return report.verdict == "pass"


def _check_sandbox_denies_sudo(result: dict[str, Any]) -> bool:
    from app.runtime.sandbox_policy import check_command

    d = check_command("sudo id")
    return d.allowed is False and d.error_code in ("privilege_escalation", "sudo") or not d.allowed


def _check_strategy_lane(result: dict[str, Any]) -> bool:
    from app.runtime.model_strategy import TaskSignals, select_strategy

    d = select_strategy(TaskSignals(needs_code=True))
    return d.lane == "coder"


def _check_memory_distill(result: dict[str, Any]) -> bool:
    from app.runtime.memory_distiller import distill_run

    out = distill_run(
        run_id="eval1",
        goal="Research Acme pricing and always prefer primary sources",
        finish_summary="- Acme pricing starts at 49 dollars per month on the official site.\n- Prefer primary sources when available.",
        journal_events=[{
            "type": "tool_call_result",
            "payload": {"name": "web_search", "result": {"ok": True, "snippet": "Acme pricing 49"}},
        }],
    )
    return out.count >= 1 and any(r.kind in ("fact", "preference") for r in out.records)


def _check_artifacts_manifest(result: dict[str, Any]) -> bool:
    import tempfile
    from pathlib import Path
    from app.runtime.artifacts import collect_workspace_artifacts

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "out.txt"
        p.write_text("hello artifact", encoding="utf-8")
        m = collect_workspace_artifacts(td, run_id="r1", goal="g")
        return m.count >= 1 and m.items[0].sha256


def _check_browser_quality(result: dict[str, Any]) -> bool:
    from app.runtime.browser.quality import score_observation

    q = score_observation({
        "url": "https://example.com",
        "title": "Example",
        "elements": [
            {"ref": "e1", "role": "button", "name": "Go", "tag": "button"},
            {"ref": "e2", "role": "link", "name": "Home", "tag": "a"},
            {"ref": "e3", "role": "textbox", "name": "Q", "tag": "input"},
        ],
    })
    return q.score >= 0.4 and not q.is_low_quality


EVAL_TASKS: list[EvalTask] = [
    EvalTask("obs-001", "Observation classifies ok tool result", "quick", _check_observation_ok, ["browser", "observation"]),
    EvalTask("plan-001", "Hierarchical plan respects dependencies", "quick", _check_plan_dag, ["plan"]),
    EvalTask("ctx-001", "Context engine compacts under budget", "standard", _check_context_budget, ["context"]),
    EvalTask("claim-001", "Claim grounding passes with evidence", "standard", _check_claim_grounding, ["verifier"]),
    EvalTask("sand-001", "Sandbox policy denies sudo", "quick", _check_sandbox_denies_sudo, ["infra", "sandbox"]),
    EvalTask("strat-001", "Model strategy selects coder lane", "quick", _check_strategy_lane, ["model"]),
    EvalTask("mem-001", "Memory distiller extracts facts", "standard", _check_memory_distill, ["memory"]),
    EvalTask("art-001", "Artifact manifest collects workspace files", "quick", _check_artifacts_manifest, ["artifacts"]),
    EvalTask("brq-001", "Browser quality scores interactive page", "quick", _check_browser_quality, ["browser"]),
    EvalTask("claim-002", "Ungrounded numeric claim fails", "standard", lambda r: __import__("app.runtime.claim_grounding", fromlist=["ground_claims"]).ground_claims(
        "Revenue grew 99 percent exclusively from secret intuition without any source.",
        journal_events=[{"type": "tool_call_result", "payload": {"result": {"snippet": "weather is fine today"}}}],
        min_coverage=0.9,
    ).verdict == "fail", ["verifier"]),
]


@dataclass
class EvalRow:
    id: str
    pass_: bool
    failure_tag: str | None
    duration_s: float
    cost_usd: float
    steps: int
    tags: list[str]

    def to_json(self) -> str:
        return json.dumps({
            "id": self.id,
            "pass": self.pass_,
            "failure_tag": self.failure_tag,
            "duration_s": round(self.duration_s, 4),
            "cost_usd": self.cost_usd,
            "steps": self.steps,
            "tags": self.tags,
        })


def _tag_failure(task: EvalTask, exc: BaseException | None) -> str:
    if exc is not None:
        return "infra"
    tags = set(task.tags)
    if "plan" in tags:
        return "plan_error"
    if "verifier" in tags:
        return "verifier_miss"
    if "observation" in tags or "browser" in tags:
        return "observation_miss"
    if "context" in tags:
        return "context_overflow"
    if "sandbox" in tags or "infra" in tags:
        return "infra"
    return "tool_error"


def run_flywheel(
    tasks: list[EvalTask] | None = None,
    *,
    out_path: str | Path | None = None,
) -> list[EvalRow]:
    """Execute tasks offline; optionally write JSONL."""
    tasks = tasks or EVAL_TASKS
    rows: list[EvalRow] = []
    for task in tasks:
        t0 = time.perf_counter()
        ok = False
        exc: BaseException | None = None
        try:
            ok = bool(task.check({}))
        except BaseException as e:
            exc = e
            ok = False
        dt = time.perf_counter() - t0
        row = EvalRow(
            id=task.id,
            pass_=ok,
            failure_tag=None if ok else _tag_failure(task, exc),
            duration_s=dt,
            cost_usd=0.0,
            steps=1,
            tags=list(task.tags),
        )
        rows.append(row)

    if out_path:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(r.to_json() for r in rows) + "\n", encoding="utf-8")

    return rows


def summarize(rows: list[EvalRow]) -> dict[str, Any]:
    total = len(rows)
    passed = sum(1 for r in rows if r.pass_)
    infra_fail = sum(1 for r in rows if r.failure_tag == "infra")
    return {
        "total": total,
        "passed": passed,
        "pass_rate": (passed / total) if total else 0.0,
        "infra_failures": infra_fail,
        "by_tag": {
            tag: sum(1 for r in rows if r.failure_tag == tag)
            for tag in FAILURE_TAGS
        },
    }


if __name__ == "__main__":
    rows = run_flywheel()
    print(json.dumps(summarize(rows), indent=2))
    for r in rows:
        print(r.to_json())
