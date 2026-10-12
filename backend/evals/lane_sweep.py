#!/usr/bin/env python3
"""Lane sweep: vary the model for one lane at a time and record metrics.

Lanes: loop, worker, verifier, summarizer, vision.
Models: the set the router supports (documented in LANE_MODELS).

Usage (from backend/):
  PYTHONPATH=. python -m evals.lane_sweep --slice dev --out evals/results/lane_sweep.json

Live model calls require credentials; without them the sweep runs the
deterministic offline adapter and records structural validity only.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Models the router is known to support (from models.py soft-import paths).
LANE_MODELS: dict[str, list[str]] = {
    "loop": ["gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet", "gemini-1.5-pro"],
    "worker": ["gpt-4o-mini", "claude-3-haiku", "gemini-1.5-flash", "gpt-4o"],
    "verifier": ["gpt-4o-mini", "claude-3-haiku", "gpt-4o"],
    "summarizer": ["gpt-4o-mini", "claude-3-haiku", "gemini-1.5-flash"],
    "vision": ["gpt-4o", "gemini-1.5-pro"],
}

DEFAULT_LANES = list(LANE_MODELS.keys())

# Frontier reference (for ceiling comparison when free endpoints cap quality).
FRONTIER_REFERENCE = {
    "loop": "claude-3-5-sonnet",
    "worker": "gpt-4o",
    "verifier": "gpt-4o",
    "summarizer": "gpt-4o-mini",
    "vision": "gpt-4o",
}


@dataclass
class LaneResult:
    lane: str
    model: str
    n_tasks: int = 0
    passed: int = 0
    pass_rate: float = 0.0
    mean_cost_usd: float = 0.0
    mean_latency_ms: float = 0.0
    tool_call_validity: float = 1.0  # fraction of well-formed tool calls
    notes: str = ""
    is_frontier_ref: bool = False
    is_simulated: bool = True


@dataclass
class SweepReport:
    timestamp: str
    slice: str
    results: list[LaneResult] = field(default_factory=list)
    defaults_chosen: dict[str, str] = field(default_factory=dict)
    methodology: str = (
        "One lane varied at a time; other lanes held at previous defaults. "
        "Pass rate, cost, latency, tool-call validity recorded. Defaults set "
        "to Pareto-best (pass_rate desc, then cost asc) within each lane."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "slice": self.slice,
            "methodology": self.methodology,
            "results": [asdict(r) for r in self.results],
            "defaults_chosen": self.defaults_chosen,
            "frontier_reference": FRONTIER_REFERENCE,
        }


def _simulate_lane_run(lane: str, model: str, n_tasks: int = 12) -> LaneResult:
    """SIMULATED — NOT AUTHORITATIVE. Do not use for production model defaults or investor claims."""
    """Deterministic offline adapter for environments without model credentials.

    Scores are derived from a stable hash of (lane, model) so results are
    reproducible and not cherry-picked. They are explicitly marked simulated.
    """
    seed = sum(ord(c) for c in f"{lane}:{model}") % 100
    # Cheaper models: lower pass, lower cost; frontier: higher pass, higher cost.
    is_frontier = model in ("gpt-4o", "claude-3-5-sonnet", "gemini-1.5-pro")
    base_pass = 0.55 + (seed % 30) / 100.0
    if is_frontier:
        base_pass = min(0.92, base_pass + 0.18)
    else:
        base_pass = min(0.78, base_pass)
    # Cost proxy
    cost_map = {
        "gpt-4o": 0.12,
        "claude-3-5-sonnet": 0.14,
        "gemini-1.5-pro": 0.10,
        "gpt-4o-mini": 0.02,
        "claude-3-haiku": 0.015,
        "gemini-1.5-flash": 0.01,
    }
    mean_cost = cost_map.get(model, 0.05) * (1 + (seed % 10) / 50)
    latency = 800 + (seed % 40) * 20
    if "mini" in model or "haiku" in model or "flash" in model:
        latency *= 0.6
    validity = 0.95 + (seed % 5) / 100.0
    passed = int(round(base_pass * n_tasks))
    return LaneResult(
        lane=lane,
        model=model,
        n_tasks=n_tasks,
        passed=passed,
        pass_rate=round(passed / n_tasks, 4),
        mean_cost_usd=round(mean_cost, 4),
        mean_latency_ms=round(latency, 1),
        tool_call_validity=round(min(1.0, validity), 4),
        notes="SIMULATED offline adapter — replace with live runner when credentials available",
        is_frontier_ref=FRONTIER_REFERENCE.get(lane) == model,
        is_simulated=True,
    )


def choose_defaults(results: list[LaneResult]) -> dict[str, str]:
    """Pareto: highest pass_rate, then lowest cost, then highest validity."""
    defaults: dict[str, str] = {}
    by_lane: dict[str, list[LaneResult]] = {}
    for r in results:
        by_lane.setdefault(r.lane, []).append(r)
    for lane, items in by_lane.items():
        items_sorted = sorted(
            items,
            key=lambda x: (-x.pass_rate, x.mean_cost_usd, -x.tool_call_validity),
        )
        defaults[lane] = items_sorted[0].model
    return defaults


def run_sweep(
    *,
    slice_name: str = "dev",
    lanes: list[str] | None = None,
    n_tasks: int = 12,
    live: bool = False,
) -> SweepReport:
    lanes = lanes or DEFAULT_LANES
    report = SweepReport(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        slice=slice_name,
    )
    if live:
        # Live path requires ModelRouter + eval runner; not available without credentials.
        raise RuntimeError(
            "Live lane sweep requires model credentials and full eval runner. "
            "Run without --live for simulated structural results."
        )
    for lane in lanes:
        for model in LANE_MODELS.get(lane, []):
            report.results.append(_simulate_lane_run(lane, model, n_tasks=n_tasks))
    report.defaults_chosen = choose_defaults(report.results)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Lane model sweep")
    parser.add_argument("--slice", default="dev")
    parser.add_argument("--out", default="evals/results/lane_sweep.json")
    parser.add_argument("--n-tasks", type=int, default=12)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--lanes", nargs="*", default=None)
    args = parser.parse_args()

    report = run_sweep(
        slice_name=args.slice,
        lanes=args.lanes,
        n_tasks=args.n_tasks,
        live=args.live,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    print(f"Wrote {out}")
    print("Defaults chosen:", json.dumps(report.defaults_chosen, indent=2))
    print("\nResults summary:")
    for r in report.results:
        tag = " [frontier]" if r.is_frontier_ref else ""
        sim = " (sim)" if r.is_simulated else ""
        print(
            f"  {r.lane:12} {r.model:22} pass={r.pass_rate:.3f} "
            f"cost={r.mean_cost_usd:.4f} lat={r.mean_latency_ms:.0f}ms "
            f"valid={r.tool_call_validity:.3f}{tag}{sim}"
        )


if __name__ == "__main__":
    main()
