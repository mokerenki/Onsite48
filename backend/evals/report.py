#!/usr/bin/env python3
"""One-command regenerable report from committed JSON (Phase 6d).

  PYTHONPATH=. python -m evals.report

Reads:
  docs/runtime/EVAL_BASELINE_DEV.json
  evals/results/long_horizon.json
  evals/results/lane_sweep.json
  evals/results/efficiency_before.json  (optional)
  evals/results/efficiency_after.json   (optional)

Writes:
  docs/proof/RESULTS.md  (tables)
  docs/proof/dashboard.html
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).parent / "results"
PROOF = ROOT / "docs" / "proof"
BASELINE = ROOT / "docs" / "runtime" / "EVAL_BASELINE_DEV.json"


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _se(p: float, n: int) -> float:
    """Standard error of a proportion."""
    if n <= 0:
        return 0.0
    return round(math.sqrt(max(0.0, p * (1 - p) / n)), 4)


def build_results_md(
    baseline: dict | None,
    lh: dict | None,
    sweep: dict | None,
    eff_before: dict | None,
    eff_after: dict | None,
) -> str:
    lines: list[str] = []
    lines.append("# RESULTS — Onsite48 Runtime Proof Package")
    lines.append("")
    lines.append("All numbers below are regenerable by:")
    lines.append("```")
    lines.append("cd backend && PYTHONPATH=. python -m evals.report")
    lines.append("```")
    lines.append("Pinned inputs: committed JSON under `backend/evals/results/` and")
    lines.append("`docs/runtime/EVAL_BASELINE_DEV.json`. Seeds are deterministic in the")
    lines.append("offline adapter. Live model runs are marked NOT RUN when credentials")
    lines.append("are absent.")
    lines.append("")

    # Baseline / legacy
    lines.append("## 1. Baseline (legacy offline adapter)")
    lines.append("")
    if baseline:
        agg = baseline.get("aggregate", {})
        overall = agg.get("overall", {})
        n = overall.get("n", 0)
        p = overall.get("pass@1", 0)
        lines.append(f"| Slice | n | pass@1 | SE | median steps | mean cost |")
        lines.append(f"|-------|---|--------|----|--------------|-----------|")
        lines.append(
            f"| overall (legacy) | {n} | {p:.3f} | {_se(p, n):.3f} | "
            f"{overall.get('median_steps', '—')} | {overall.get('mean_cost', 0):.4f} |"
        )
        for tier, t in (agg.get("tiers") or {}).items():
            tn, tp = t.get("n", 0), t.get("pass@1", 0)
            lines.append(
                f"| {tier} | {tn} | {tp:.3f} | {_se(tp, tn):.3f} | — | — |"
            )
        lines.append("")
        lines.append(f"_Note: {agg.get('note', '')}_")
    else:
        lines.append("_Baseline JSON missing._")
    lines.append("")

    # Long-horizon
    lines.append("## 2. Long-horizon reliability (Phase 6a)")
    lines.append("")
    if lh:
        agg = lh.get("aggregate", {})
        n = agg.get("n", 0)
        passed = agg.get("passed", 0)
        pr = agg.get("pass_rate", 0)
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Tasks | {n} |")
        lines.append(f"| Passed | {passed} |")
        lines.append(f"| Completion rate | {pr:.3f} ± {_se(pr, n):.3f} |")
        lines.append(f"| Mean cost USD | {agg.get('mean_cost_usd', 0):.4f} |")
        lines.append(f"| Cost per success | {agg.get('cost_per_success') or '—'} |")
        lines.append(f"| Mean steps | {agg.get('mean_steps', 0)} |")
        lines.append(f"| Mean tokens/step | {agg.get('mean_tokens_per_step', 0)} |")
        lines.append(f"| Mean cache hit rate | {agg.get('mean_cache_hit_rate') or 'n/a'} |")
        lines.append("")
        lines.append("### Per-task")
        lines.append("")
        lines.append("| run (truncated) | success | steps | cost | compactions | pauses | restarts | failure_cause |")
        lines.append("|---------------|---------|-------|------|-------------|--------|----------|---------------|")
        for r in lh.get("runs", []):
            lines.append(
                f"| {r['run_id'][:8]} | {r['success']} | {r['steps']} | "
                f"${r['total_cost_usd']:.4f} | {r.get('compaction_count', 0)} | "
                f"{r.get('approval_pauses', 0)} | {r.get('restarts', 0)} | "
                f"{r.get('failure_cause') or '—'} |"
            )
        lines.append("")
        lines.append("_Source: simulated offline adapter. Live long-horizon: NOT RUN (no credentials)._")
    else:
        lines.append("_long_horizon.json missing — run `python -m evals.long_horizon`._")
    lines.append("")

    # Efficiency before/after
    lines.append("## 3. Cost / efficiency before → after (Phase 6b)")
    lines.append("")
    if eff_before and eff_after:
        ba, aa = eff_before.get("aggregate", {}), eff_after.get("aggregate", {})
        lines.append("| Metric | Before | After | Δ |")
        lines.append("|--------|--------|-------|---|")
        for key, label in [
            ("mean_cost_usd", "Mean cost USD"),
            ("mean_tokens_per_step", "Tokens/step"),
            ("mean_steps", "Steps/task"),
            ("mean_cache_hit_rate", "Cache hit rate"),
            ("pass_rate", "Pass rate"),
        ]:
            b, a = ba.get(key), aa.get(key)
            if b is None and a is None:
                continue
            b_s = f"{b:.4f}" if isinstance(b, float) else str(b)
            a_s = f"{a:.4f}" if isinstance(a, float) else str(a)
            try:
                delta = f"{(a or 0) - (b or 0):+.4f}"
            except TypeError:
                delta = "—"
            lines.append(f"| {label} | {b_s} | {a_s} | {delta} |")
        lines.append("")
        lines.append("Fixes applied: stable system-prompt prefix, tool-output offload,")
        lines.append("redundant-call guard, compaction metrics exposure.")
    else:
        lines.append("_Efficiency before/after JSON not yet present; structural fixes committed in metrics.py / context path._")
    lines.append("")

    # Lane sweep
    lines.append("## 4. Lane sweep (Phase 6c)")
    lines.append("")
    if sweep:
        lines.append(f"Timestamp: {sweep.get('timestamp')}")
        lines.append(f"Slice: {sweep.get('slice')}")
        lines.append("")
        lines.append("| Lane | Model | pass_rate | cost | latency_ms | tool_validity | frontier | sim |")
        lines.append("|------|-------|-----------|------|------------|---------------|----------|-----|")
        for r in sweep.get("results", []):
            lines.append(
                f"| {r['lane']} | {r['model']} | {r['pass_rate']:.3f} | "
                f"{r['mean_cost_usd']:.4f} | {r['mean_latency_ms']:.0f} | "
                f"{r['tool_call_validity']:.3f} | "
                f"{'Y' if r.get('is_frontier_ref') else ''} | "
                f"{'Y' if r.get('is_simulated') else ''} |"
            )
        lines.append("")
        lines.append("### Defaults chosen from data")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(sweep.get("defaults_chosen", {}), indent=2))
        lines.append("```")
        lines.append("")
        lines.append("Free/cheap endpoints show a measurable ceiling vs frontier reference")
        lines.append("(see pass_rate gap in the table). Defaults prefer the Pareto front")
        lines.append("(pass_rate, then cost).")
        lines.append("")
        lines.append("_Simulated offline adapter. Live sweep: NOT RUN (no credentials)._")
    else:
        lines.append("_lane_sweep.json missing — run `python -m evals.lane_sweep`._")
    lines.append("")

    # Held-out / GAIA placeholders
    lines.append("## 5. Held-out and external (GAIA subset)")
    lines.append("")
    lines.append("| Slice | n | pass@1 | SE | notes |")
    lines.append("|-------|---|--------|----|-------|")
    lines.append("| human-run held-out | — | — | — | NOT RUN — held-out set not present in workspace |")
    lines.append("| GAIA subset | — | — | — | NOT RUN — GAIA files not present; never read for tuning |")
    lines.append("| frontier reference (live) | — | — | — | NOT RUN — no model credentials |")
    lines.append("")
    lines.append("Dev-versus-held-out gap: **not measurable** until held-out is run.")
    lines.append("")

    lines.append("## Methodology (stranger can rerun)")
    lines.append("")
    lines.append("1. `cd backend && PYTHONPATH=. python -m evals.long_horizon`")
    lines.append("2. `PYTHONPATH=. python -m evals.lane_sweep`")
    lines.append("3. `PYTHONPATH=. python -m evals.report`")
    lines.append("4. Inspect `docs/proof/RESULTS.md` and `evals/results/*.json`.")
    lines.append("")
    lines.append("Versions: runtime Phase 6; offline adapter seeds are pure functions of task id.")
    lines.append("")
    return "\n".join(lines)


def build_dashboard_html(
    baseline: dict | None,
    lh: dict | None,
    sweep: dict | None,
) -> str:
    """Minimal static dashboard from runner JSON."""
    points = []
    if baseline:
        p = baseline.get("aggregate", {}).get("overall", {}).get("pass@1", 0)
        points.append({"commit": "baseline-legacy", "pass_rate": p})
    if lh:
        points.append(
            {
                "commit": "phase6-long-horizon",
                "pass_rate": lh.get("aggregate", {}).get("pass_rate", 0),
            }
        )
    if sweep:
        # average pass across frontier refs
        refs = [r for r in sweep.get("results", []) if r.get("is_frontier_ref")]
        if refs:
            avg = sum(r["pass_rate"] for r in refs) / len(refs)
            points.append({"commit": "phase6-lane-frontier", "pass_rate": round(avg, 4)})

    data_json = json.dumps(points)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Onsite48 Runtime — Pass Rate Trajectory</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem; background: #0b0f14; color: #e6edf3; }}
  h1 {{ font-size: 1.4rem; }}
  .card {{ background: #161b22; border-radius: 8px; padding: 1.25rem; margin-bottom: 1rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: 0.4rem 0.75rem; border-bottom: 1px solid #30363d; }}
  .bar {{ height: 18px; background: #238636; border-radius: 3px; }}
  .note {{ color: #8b949e; font-size: 0.85rem; }}
</style>
</head>
<body>
<h1>Onsite48 Runtime — Pass Rate over Commit History</h1>
<p class="note">Generated from committed runner JSON. Live model numbers appear when credentials are available.</p>
<div class="card">
<table id="tbl"><thead><tr><th>Commit / milestone</th><th>Pass rate</th><th></th></tr></thead><tbody></tbody></table>
</div>
<script>
const data = {data_json};
const tbody = document.querySelector('#tbl tbody');
data.forEach(d => {{
  const tr = document.createElement('tr');
  const pct = (d.pass_rate * 100).toFixed(1);
  tr.innerHTML = `<td>${{d.commit}}</td><td>${{pct}}%</td>
    <td><div class="bar" style="width:${{Math.max(4, d.pass_rate*100)}}%"></div></td>`;
  tbody.appendChild(tr);
}});
</script>
</body>
</html>
"""


def main() -> None:
    baseline = _load(BASELINE)
    lh = _load(RESULTS / "long_horizon.json")
    sweep = _load(RESULTS / "lane_sweep.json")
    eff_before = _load(RESULTS / "efficiency_before.json")
    eff_after = _load(RESULTS / "efficiency_after.json")

    PROOF.mkdir(parents=True, exist_ok=True)
    md = build_results_md(baseline, lh, sweep, eff_before, eff_after)
    (PROOF / "RESULTS.md").write_text(md, encoding="utf-8")
    html = build_dashboard_html(baseline, lh, sweep)
    (PROOF / "dashboard.html").write_text(html, encoding="utf-8")
    print(f"Wrote {PROOF / 'RESULTS.md'}")
    print(f"Wrote {PROOF / 'dashboard.html'}")


if __name__ == "__main__":
    main()
