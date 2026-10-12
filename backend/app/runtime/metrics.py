"""Cost, token, cache, and efficiency metrics for the agent loop (Phase 6).

All numbers are regenerable from RunState + Journal events. Prompt-cache hit
rate is recorded when the provider response exposes it; otherwise None.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Fallback USD rates when provider does not return cost_usd (per 1M tokens).
# These are not used for billing — only for offline / simulated accounting.
FALLBACK_RATES: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-haiku": {"input": 0.25, "output": 1.25},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    "default": {"input": 1.00, "output": 3.00},
}


@dataclass
class StepMetrics:
    step: int
    model: str
    lane: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int | None = None  # prompt-cache read tokens if exposed
    cache_write_tokens: int | None = None
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    tool_calls: int = 0
    tool_names: list[str] = field(default_factory=list)
    context_chars: int = 0
    compacted: bool = False


@dataclass
class RunMetrics:
    run_id: str
    goal: str = ""
    status: str = ""
    steps: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_hit_tokens: int = 0
    total_cost_usd: float = 0.0
    wall_s: float = 0.0
    tokens_per_step: float = 0.0
    cost_per_step: float = 0.0
    cache_hit_rate: float | None = None  # hit / (hit + write + uncached) when known
    steps_per_task: int = 0
    success: bool = False
    failure_cause: str | None = None
    lane_costs: dict[str, float] = field(default_factory=dict)
    step_details: list[StepMetrics] = field(default_factory=list)
    compaction_count: int = 0
    approval_pauses: int = 0
    restarts: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cache_hit_tokens: int = 0,
) -> float:
    """Estimate USD cost. Cache hits billed at ~10% of input rate when known."""
    rates = FALLBACK_RATES.get(model, FALLBACK_RATES["default"])
    uncached = max(0, input_tokens - cache_hit_tokens)
    cost = (uncached / 1_000_000) * rates["input"]
    cost += (cache_hit_tokens / 1_000_000) * rates["input"] * 0.1
    cost += (output_tokens / 1_000_000) * rates["output"]
    return round(cost, 6)


def aggregate_run_metrics(
    run_id: str,
    *,
    goal: str = "",
    status: str = "",
    steps: list[StepMetrics] | None = None,
    wall_s: float = 0.0,
    success: bool = False,
    failure_cause: str | None = None,
    compaction_count: int = 0,
    approval_pauses: int = 0,
    restarts: int = 0,
) -> RunMetrics:
    steps = steps or []
    total_in = sum(s.input_tokens for s in steps)
    total_out = sum(s.output_tokens for s in steps)
    total_hit = sum(s.cache_hit_tokens or 0 for s in steps)
    total_cost = sum(s.cost_usd for s in steps)
    n = len(steps) or 1
    hit_rate = None
    if total_hit > 0 or any(s.cache_hit_tokens is not None for s in steps):
        denom = total_in or 1
        hit_rate = round(total_hit / denom, 4)
    lane_costs: dict[str, float] = {}
    for s in steps:
        lane_costs[s.lane] = lane_costs.get(s.lane, 0.0) + s.cost_usd
    return RunMetrics(
        run_id=run_id,
        goal=goal,
        status=status,
        steps=len(steps),
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        total_cache_hit_tokens=total_hit,
        total_cost_usd=round(total_cost, 6),
        wall_s=wall_s,
        tokens_per_step=round((total_in + total_out) / n, 1),
        cost_per_step=round(total_cost / n, 6),
        cache_hit_rate=hit_rate,
        steps_per_task=len(steps),
        success=success,
        failure_cause=failure_cause,
        lane_costs=lane_costs,
        step_details=steps,
        compaction_count=compaction_count,
        approval_pauses=approval_pauses,
        restarts=restarts,
    )


def success_per_dollar(metrics: list[RunMetrics]) -> float | None:
    successes = sum(1 for m in metrics if m.success)
    total_cost = sum(m.total_cost_usd for m in metrics)
    if total_cost <= 0:
        return None
    return round(successes / total_cost, 4)


def cost_per_successful_task(metrics: list[RunMetrics]) -> float | None:
    successful = [m for m in metrics if m.success]
    if not successful:
        return None
    return round(sum(m.total_cost_usd for m in successful) / len(successful), 6)


def write_metrics_json(path: Path, metrics: list[RunMetrics]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "runs": [m.to_dict() for m in metrics],
        "aggregate": {
            "n": len(metrics),
            "passed": sum(1 for m in metrics if m.success),
            "pass_rate": round(sum(1 for m in metrics if m.success) / max(1, len(metrics)), 4),
            "mean_cost_usd": round(sum(m.total_cost_usd for m in metrics) / max(1, len(metrics)), 6),
            "cost_per_success": cost_per_successful_task(metrics),
            "success_per_dollar": success_per_dollar(metrics),
            "mean_steps": round(sum(m.steps for m in metrics) / max(1, len(metrics)), 1),
            "mean_tokens_per_step": round(
                sum(m.tokens_per_step for m in metrics) / max(1, len(metrics)), 1
            ),
            "mean_cache_hit_rate": (
                round(
                    sum(m.cache_hit_rate for m in metrics if m.cache_hit_rate is not None)
                    / max(1, sum(1 for m in metrics if m.cache_hit_rate is not None)),
                    4,
                )
                if any(m.cache_hit_rate is not None for m in metrics)
                else None
            ),
        },
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# Efficiency fixes applied in Phase 6 (see context.py / loop.py patches):
# 1. Stable system prompt prefix (versioned file, no dynamic timestamps).
# 2. Tool output offload + truncation already in context.offload_large_output.
# 3. Redundant read detection via recent_calls hash in loop.
# 4. Compaction keeps system + last N turns; records compaction_count.
EFFICIENCY_NOTES = """
Phase 6 efficiency changes:
- system prompt loaded once from versioned file (stable prefix for cache).
- tool results > budget chars offloaded to workspace; only head/tail in context.
- same (tool, args_hash) repeated LOOP_SAME_CALL_LIMIT times → structured error.
- compaction_count exposed on RunMetrics for long-horizon analysis.
"""
