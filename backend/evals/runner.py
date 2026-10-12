
"""Outcome-based eval runner for Onsite48 Phase 0."""
from __future__ import annotations
import argparse, asyncio, json, os, statistics, time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
import httpx, yaml

EVALS_ROOT = Path(__file__).resolve().parent
TASKS_ROOT = EVALS_ROOT / "tasks"
RESULTS_ROOT = EVALS_ROOT / "results"
GOOGLE_FAKE = "http://127.0.0.1:8765"
WEB_FAKE = "http://127.0.0.1:8766"
FAKE_TOKEN = "eval-fake-token"

@dataclass
class TaskResult:
    id: str; tier: str; status: str; verdict: bool
    steps: int = 0; tokens: int = 0; cost_usd: Optional[float] = None
    cache_read_tokens: Optional[int] = None; cache_write_tokens: Optional[int] = None
    wall_time_s: float = 0.0; grader_notes: List[str] = field(default_factory=list)
    error: Optional[str] = None; transcript: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)

def load_tasks(split, tiers=None, ids=None):
    split_dir = TASKS_ROOT / split
    if not split_dir.is_dir(): return []
    tasks = []
    for path in sorted(split_dir.glob("*.yaml")):
        t = yaml.safe_load(open(path))
        if not t: continue
        if tiers and t.get("tier") not in tiers: continue
        if ids and t.get("id") not in ids: continue
        tasks.append(t)
    return tasks

def _healthy():
    try:
        return (httpx.get(f"{GOOGLE_FAKE}/__health", timeout=1).status_code == 200
                and httpx.get(f"{WEB_FAKE}/__health", timeout=1).status_code == 200)
    except Exception:
        return False

def ensure_fakes():
    if _healthy(): return
    import threading, uvicorn
    def run(app_path, port):
        uvicorn.run(app_path, host="127.0.0.1", port=port, log_level="error", access_log=False)
    threading.Thread(target=run, args=("evals.fakes.google.app:app", 8765), daemon=True).start()
    threading.Thread(target=run, args=("evals.fakes.web.app:app", 8766), daemon=True).start()
    for _ in range(50):
        if _healthy(): return
        time.sleep(0.1)
    raise RuntimeError("Eval fakes failed to start")

def seed_fixtures(task):
    fixtures = task.get("fixtures") or {}
    google = fixtures.get("google")
    if google is not None:
        httpx.post(f"{GOOGLE_FAKE}/__seed", json=google, timeout=5)
    else:
        httpx.post(f"{GOOGLE_FAKE}/__reset", json={}, timeout=5)
    if fixtures.get("browser_reset"):
        httpx.post(f"{WEB_FAKE}/__browser_reset", timeout=5)

def configure_env():
    os.environ["ONSITE48_GOOGLE_API_BASE"] = GOOGLE_FAKE
    os.environ["ONSITE48_FAKE_GOOGLE_TOKEN"] = FAKE_TOKEN

async def run_one(task, runtime, *, repeat_idx: int = 0, allow_offline_fallback: bool = False):
    seed_fixtures(task)
    t0 = time.time()
    try:
        if runtime in ("legacy", "scripted_oracle"):
            # Scripted oracle against fakes — not an LLM baseline.
            from evals.adapters.legacy_adapter import run_legacy
            result = await run_legacy(task)
            if hasattr(result, "status") and result.status:
                pass
            setattr(result, "_baseline_kind", "scripted_oracle")
        elif runtime == "loop":
            import uuid
            # Credentials gate
            has_keys = bool(os.environ.get("KIMI_API_KEY") or os.environ.get("MOONSHOT_API_KEY") or os.environ.get("DEEPSEEK_API_KEY"))
            if not has_keys and not allow_offline_fallback:
                return TaskResult(
                    id=task["id"], tier=task.get("tier", ""), status="infra_error",
                    verdict=False, error="missing API keys for loop runtime; pass --allow-offline-fallback to proceed",
                    wall_time_s=time.time() - t0,
                )
            from app.runtime.loop import run as loop_run
            goal = task.get("goal") or task.get("prompt") or task.get("instruction") or ""
            task_id = str(task.get("id") or "eval")
            # Unique run_id so repeats never replay a finished journal
            run_id = f"{task_id}__{repeat_idx}__{uuid.uuid4().hex[:8]}"
            role_config = dict(task.get("role_config") or {})
            role_config.setdefault("unattended", True)
            loop_out = await loop_run(run_id, goal, role_config)
            status = "passed"
            answer = None
            steps = 0
            tokens = 0
            cost_usd = None
            if isinstance(loop_out, dict):
                st = str(loop_out.get("status") or "")
                payload = loop_out.get("result") or loop_out.get("finish_payload") or {}
                if isinstance(payload, dict):
                    answer = payload.get("summary") or payload.get("final_answer") or payload.get("message")
                # Prefer top-level metrics; fall back to nested state for resilience
                state = loop_out.get("state") if isinstance(loop_out.get("state"), dict) else {}
                steps = int(
                    loop_out.get("step_count")
                    or state.get("step_count")
                    or loop_out.get("steps")
                    or 0
                )
                tokens = int(
                    loop_out.get("token_count")
                    or state.get("token_count")
                    or loop_out.get("tokens")
                    or 0
                )
                cost_raw = loop_out.get("cost_usd")
                if cost_raw is None:
                    cost_raw = state.get("cost_usd")
                cost_usd = float(cost_raw) if cost_raw is not None else None
                # Status taxonomy: infra vs incomplete vs agent failure vs success
                if st in ("infra_error",) or loop_out.get("error_code") in ("auth_failed", "missing_credentials"):
                    status = "infra_error"
                elif st in ("partial", "needs_input", "needs_user", "waiting_approval"):
                    status = "incomplete"
                elif st in ("failed", "cancelled", "error", "budget_exhausted"):
                    status = "failed"
                elif st in ("completed", "completed_unverified", "finished", "passed", ""):
                    status = "passed"
                else:
                    status = "failed"
            result = TaskResult(
                id=task["id"],
                tier=task.get("tier", ""),
                status=status,
                verdict=(status == "passed"),
                steps=steps,
                tokens=tokens,
                # Keep cost_usd=None when unknown; do not coerce unknown to 0.0
                cost_usd=cost_usd,
                error=None if status not in ("failed", "error", "infra_error") else str(loop_out)[:500],
            )
            setattr(result, "_agent_answer", answer)
            setattr(result, "_loop_raw", loop_out)
            setattr(result, "_run_id", run_id)
            setattr(result, "_offline", not has_keys)
        else:
            result = TaskResult(id=task["id"], tier=task.get("tier", ""), status="error",
                                verdict=False, error=f"unknown runtime {runtime}")
    except Exception as exc:
        msg = str(exc).lower()
        is_infra = any(m in msg for m in ("api key", "auth", "unauthorized", "credentials", "not configured"))
        return TaskResult(
            id=task["id"], tier=task.get("tier", ""),
            status="infra_error" if is_infra else "error",
            verdict=False, error=str(exc), wall_time_s=time.time() - t0,
        )
    try:
        from evals.graders.base import grade_task
        grade = grade_task(
            task.get("grader") or {},
            google_state=httpx.get(f"{GOOGLE_FAKE}/__state", timeout=5).json(),
            browser_state=httpx.get(f"{WEB_FAKE}/__browser_state", timeout=5).json(),
            agent_answer=getattr(result, "_agent_answer", None),
        )
        result.verdict = grade.passed
        result.status = "passed" if grade.passed else "failed"
        result.grader_notes = grade.notes
    except Exception as exc:
        result.status = "error"
        result.verdict = False
        result.error = (result.error or "") + f" | grade: {exc}"
    result.wall_time_s = time.time() - t0
    return result

def aggregate(results, repeat):
    by_tier = {}
    for r in results:
        by_tier.setdefault(r.tier, []).append(r)
    def stats(rs):
        n = len(rs)
        passed = sum(1 for r in rs if r.verdict)
        steps = [r.steps for r in rs]
        costs = [r.cost_usd for r in rs if r.cost_usd is not None]
        succ = [r.cost_usd for r in rs if r.verdict and r.cost_usd is not None]
        return {
            "n": n, "pass@1": passed / n if n else 0.0, "passed": passed,
            "median_steps": statistics.median(steps) if steps else 0,
            "p90_steps": sorted(steps)[int(0.9 * (len(steps) - 1))] if steps else 0,
            "mean_cost": statistics.mean(costs) if costs else None,
            "cost_per_success": (sum(succ) / len(succ)) if succ else None,
        }
    by_id = {}
    for r in results:
        by_id.setdefault(r.id, []).append(r)
    per_task_se = {}
    if repeat > 1:
        for tid, rs in by_id.items():
            rates = [1.0 if r.verdict else 0.0 for r in rs]
            per_task_se[tid] = statistics.stdev(rates) / (len(rates) ** 0.5) if len(rates) > 1 else 0.0
    return {
        "overall": stats(results),
        "tiers": {t: stats(rs) for t, rs in by_tier.items()},
        "per_task_se": per_task_se,
        "repeat": repeat,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

def markdown_table(agg, results):
    lines = [
        f"# Eval results (repeat={agg['repeat']})", "",
        f"Generated: {agg['generated_at']}", "", "## Overall",
        f"- pass@1: {agg['overall']['pass@1']:.3f} ({agg['overall']['passed']}/{agg['overall']['n']})",
        f"- median steps: {agg['overall']['median_steps']}",
        f"- p90 steps: {agg['overall']['p90_steps']}",
        f"- mean cost USD: {agg['overall']['mean_cost']:.4f}",
        f"- cost/success: {agg['overall']['cost_per_success']}", "",
        "## By tier",
        "| tier | n | pass@1 | median steps | p90 steps | mean cost | cost/success |",
        "|------|---|--------|--------------|-----------|-----------|--------------|",
    ]
    for t, s in sorted(agg["tiers"].items()):
        cps = f"{s['cost_per_success']:.4f}" if s["cost_per_success"] is not None else "n/a"
        lines.append(
            f"| {t} | {s['n']} | {s['pass@1']:.3f} | {s['median_steps']} | {s['p90_steps']} | {s['mean_cost']:.4f} | {cps} |"
        )
    lines += ["", "## Per-task", "| id | tier | status | steps | notes |",
              "|----|------|--------|-------|-------|"]
    for r in results:
        notes = "; ".join(r.grader_notes)[:80]
        lines.append(f"| {r.id} | {r.tier} | {r.status} | {r.steps} | {notes} |")
    return "\n".join(lines) + "\n"

async def amain(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--runtime", choices=["legacy", "scripted_oracle", "loop"], default="scripted_oracle")
    p.add_argument("--allow-offline-fallback", action="store_true",
                   help="Allow loop runtime without API keys (labeled offline in results)")
    p.add_argument("--split", choices=["dev", "heldout", "external"], default="dev")
    p.add_argument("--tiers", nargs="*")
    p.add_argument("--ids", nargs="*")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--out")
    p.add_argument("--fast", action="store_true")
    args = p.parse_args(list(argv) if argv is not None else None)
    if args.split in ("heldout", "external"):
        sd = TASKS_ROOT / args.split
        if not sd.is_dir() or not any(sd.glob("*.yaml")):
            print("HELDOUT NOT PRESENT" if args.split == "heldout" else "EXTERNAL NOT PRESENT")
            return 0
    configure_env()
    ensure_fakes()
    ids = args.ids
    if args.fast:
        ids = ["rel-email-draft-001", "rel-cal-create-001", "rel-email-search-001",
               "gen-qa-001", "br-login-wall-001", "br-destructive-001"]
    tasks = load_tasks(args.split, tiers=args.tiers, ids=ids)
    if not tasks:
        print("No tasks"); return 1
    print(f"Running {len(tasks)} tasks x {args.repeat}")
    all_results = []
    for rep in range(args.repeat):
        for task in tasks:
            print(f"  [{rep+1}] {task['id']} ...", flush=True)
            result = await run_one(task, args.runtime, repeat_idx=rep, allow_offline_fallback=args.allow_offline_fallback)
            print(f"    -> {result.status}")
            all_results.append(result)
    agg = aggregate(all_results, args.repeat)
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_json = Path(args.out) if args.out else RESULTS_ROOT / f"{args.runtime}_{args.split}_{stamp}.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"aggregate": agg, "results": [asdict(r) for r in all_results],
               "runtime": args.runtime, "split": args.split},
              open(out_json, "w"), indent=2, default=str)
    md = markdown_table(agg, all_results)
    open(out_json.with_suffix(".md"), "w").write(md)
    print(md)
    print(f"Wrote {out_json}")
    return 0

def main():
    raise SystemExit(asyncio.run(amain()))

if __name__ == "__main__":
    main()
