"""Gap 1: loop.run terminal returns expose top-level metrics; runner reads them."""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _extract_metrics(loop_out: dict) -> tuple[int, int, float | None]:
    """Mirror evals/runner.py metric extraction for unit testing."""
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
    return steps, tokens, cost_usd


def test_metrics_from_top_level():
    loop_out = {
        "status": "completed",
        "result": {"summary": "ok"},
        "state": {"step_count": 0, "token_count": 0, "cost_usd": 0.0},
        "step_count": 3,
        "token_count": 1500,
        "cost_usd": 0.012,
        "run_id": "r1",
    }
    steps, tokens, cost = _extract_metrics(loop_out)
    assert steps == 3
    assert tokens == 1500
    assert cost == pytest.approx(0.012)


def test_metrics_from_nested_state_only():
    """Runner must still populate TaskResult when metrics live only under state."""
    loop_out = {
        "status": "completed",
        "result": {"summary": "ok"},
        "state": {
            "step_count": 2,
            "token_count": 400,
            "cost_usd": 0.003,
        },
    }
    steps, tokens, cost = _extract_metrics(loop_out)
    assert steps == 2
    assert tokens == 400
    assert cost == pytest.approx(0.003)


def test_metrics_unknown_cost_stays_none():
    loop_out = {
        "status": "partial",
        "result": {"summary": "budget"},
        "state": {"step_count": 1, "token_count": 10},
        "step_count": 1,
        "token_count": 10,
        "cost_usd": None,
    }
    steps, tokens, cost = _extract_metrics(loop_out)
    assert steps == 1
    assert tokens == 10
    assert cost is None


def test_terminal_return_helper_includes_metrics():
    from app.runtime.loop import _terminal_return

    state = SimpleNamespace(
        step_count=4,
        token_count=999,
        cost_usd=0.05,
        to_dict=lambda: {"step_count": 4, "token_count": 999, "cost_usd": 0.05},
    )
    out = _terminal_return("completed", {"summary": "done"}, state, run_id="abc")
    assert out["status"] == "completed"
    assert out["step_count"] == 4
    assert out["token_count"] == 999
    assert out["cost_usd"] == 0.05
    assert out["run_id"] == "abc"
    assert out["state"]["step_count"] == 4
