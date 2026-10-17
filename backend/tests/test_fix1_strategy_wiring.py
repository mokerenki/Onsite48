"""Tests for Fix 1 — model strategy wiring via goal signals."""
from __future__ import annotations

from app.runtime.models import _decide_strategy


def test_decide_strategy_uses_goal_signals():
    d = _decide_strategy(
        role="loop",
        goal="compare three frameworks with sources and cite them",
        research_depth="standard",
        tools_used=[],
    )
    assert d is not None and d.lane in ("researcher", "planner")
