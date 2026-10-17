"""Tests for Fix 2 — hierarchical plan driver lifecycle."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.runtime import plan_driver


class _FakeJournal:
    """Minimal journal stub supporting list_events + append."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def list_events(self, run_id: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("run_id") == run_id]

    async def append(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"run_id": run_id, "type": event_type, "payload": payload})


class MockPlannerRouter:
    """Router that returns a valid hierarchical-plan JSON payload."""

    async def route(
        self,
        role: str,
        messages: Any,
        *,
        tools: Any = None,
        tool_choice: Any = None,
        temperature: Any = None,
        max_tokens: Any = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            content=(
                '{"refined_goal": "research and summarize X", '
                '"sub_goals": [{"id": "sg1", "description": "Research X", '
                '"steps": [{"step_id": "s1", "description": "Search X", '
                '"tool_name": "web_search", "depends_on": []}], '
                '"success_condition": "X researched"}], '
                '"success_conditions": ["X summarized"]}'
            ),
            model="mock-planner",
            provider="mock",
            usage={},
            cost_usd=0.0,
        )


@pytest.mark.asyncio
async def test_plan_lifecycle():
    plan_driver.ENABLE_HIERARCHICAL_PLANS = True
    journal = _FakeJournal()
    specs: dict[str, Any] = {}
    ok = await plan_driver.maybe_create_plan(
        run_id="r1",
        goal="research and summarize X",
        specs=specs,
        journal=journal,
        model_router=MockPlannerRouter(),
    )
    assert ok
    msg = plan_driver.plan_context_message("r1")
    assert msg is not None
    assert "[Hierarchical plan]" in msg["content"]

    await plan_driver.mark_step_running(run_id="r1", tool_name="web_search")
    await plan_driver.advance_plan(
        run_id="r1", tool_name="web_search", ok=True, journal=journal
    )
    plan = plan_driver._get_plan("r1")
    assert plan is not None
    assert any(n.status == "done" for n in plan.nodes.values() if n.kind == "step")
