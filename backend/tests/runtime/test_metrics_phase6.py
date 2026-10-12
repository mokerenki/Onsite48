"""Phase 6 metrics and lane-default unit tests."""
from __future__ import annotations

from app.runtime.metrics import (
    StepMetrics,
    aggregate_run_metrics,
    cost_per_successful_task,
    estimate_cost,
    success_per_dollar,
)
from app.runtime.lane_defaults import LANE_DEFAULTS, is_supported_model, model_for_lane


def test_estimate_cost_positive():
    c = estimate_cost("gpt-4o-mini", 1000, 200)
    assert c > 0


def test_aggregate_and_success_metrics():
    steps = [
        StepMetrics(step=1, model="gpt-4o-mini", lane="loop", input_tokens=1000, output_tokens=100, cost_usd=0.01),
        StepMetrics(step=2, model="gpt-4o-mini", lane="loop", input_tokens=1000, output_tokens=100, cost_usd=0.01, cache_hit_tokens=400),
    ]
    m = aggregate_run_metrics("r1", steps=steps, success=True)
    assert m.steps == 2
    assert m.total_cost_usd == 0.02
    assert m.cache_hit_rate is not None
    assert cost_per_successful_task([m]) == 0.02
    assert success_per_dollar([m]) == 50.0


def test_lane_defaults_cover_all_lanes():
    for lane in ("loop", "worker", "verifier", "summarizer", "vision"):
        assert lane in LANE_DEFAULTS
        assert model_for_lane(lane)
        # Product defaults must only name models the router has clients for.
        assert is_supported_model(model_for_lane(lane))
    # Unknown lanes fall back to the primary model rather than raising.
    assert model_for_lane("not-a-lane")
