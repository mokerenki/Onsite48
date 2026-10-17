"""Workstream A: plan integration helpers."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.runtime.plan_integration import (
    advance_plan_on_tool,
    bootstrap_plan,
    finish_should_retry_for_plan,
    should_bootstrap_plan,
)


def test_should_bootstrap_research_goal():
    assert should_bootstrap_plan("Research competitor pricing and compare sources", research_depth="standard")


def test_skip_fast_lane():
    assert not should_bootstrap_plan("Title this chat", is_fast_lane=True)


def test_bootstrap_creates_steps():
    payload = bootstrap_plan("Research Acme pricing and cite sources", research_depth="deep")
    assert payload is not None
    assert payload.get("goal")
    nodes = payload.get("nodes") or {}
    steps = [n for n in nodes.values() if n.get("kind") == "step"]
    assert len(steps) >= 2


def test_advance_marks_ready_done():
    payload = bootstrap_plan("Implement a small Python fix and run tests")
    assert payload
    updated = advance_plan_on_tool(payload, tool_name="python_exec", ok=True, result_summary="tests passed")
    assert updated
    steps = [n for n in (updated.get("nodes") or {}).values() if n.get("kind") == "step"]
    assert any(n.get("status") == "done" for n in steps)


def test_finish_retry_while_open():
    payload = bootstrap_plan("Build a report with research and synthesis", research_depth="standard")
    retry, open_steps = finish_should_retry_for_plan(payload, finish_attempts=1, max_attempts=2)
    assert retry is True
    assert open_steps
    retry2, _ = finish_should_retry_for_plan(payload, finish_attempts=2, max_attempts=2)
    assert retry2 is False
