"""Phase 2: hierarchical plan unit tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.runtime.hierarchical_plan import HierarchicalPlan


def test_from_flat_steps_ready_and_deps():
    plan = HierarchicalPlan.from_flat_steps(
        "Build a report",
        [
            {"step_id": "s1", "description": "Research", "tool_name": "web_search"},
            {"step_id": "s2", "description": "Write", "depends_on": ["s1"], "tool_name": "file_write"},
            {"step_id": "s3", "description": "Deliver", "depends_on": ["s2"]},
        ],
    )
    ready = plan.ready_steps()
    assert [r.id for r in ready] == ["s1"]
    plan.mark_done("s1")
    assert [r.id for r in plan.ready_steps()] == ["s2"]
    plan.mark_done("s2")
    assert [r.id for r in plan.ready_steps()] == ["s3"]
    plan.mark_done("s3")
    assert plan.is_complete()


def test_from_payload_subgoals():
    payload = {
        "refined_goal": "Compare two vendors",
        "success_conditions": ["Both vendors covered"],
        "sub_goals": [
            {
                "id": "sg_a",
                "description": "Vendor A",
                "steps": [
                    {"step_id": "a1", "description": "Search A"},
                    {"step_id": "a2", "description": "Summarize A", "depends_on": ["a1"]},
                ],
            },
            {
                "id": "sg_b",
                "description": "Vendor B",
                "steps": [
                    {"step_id": "b1", "description": "Search B"},
                ],
            },
        ],
    }
    plan = HierarchicalPlan.from_payload(payload)
    ready_ids = {r.id for r in plan.ready_steps()}
    assert "a1" in ready_ids
    assert "b1" in ready_ids
    assert "a2" not in ready_ids
    plan.mark_done("a1")
    assert "a2" in {r.id for r in plan.ready_steps()}


def test_cycle_rejected():
    with pytest.raises(ValueError, match="cycle"):
        HierarchicalPlan.from_payload(
            {
                "goal": "bad",
                "plan": [
                    {"step_id": "s1", "description": "A", "depends_on": ["s2"]},
                    {"step_id": "s2", "description": "B", "depends_on": ["s1"]},
                ],
            }
        )


def test_unknown_dep_rejected():
    with pytest.raises(ValueError, match="unknown"):
        HierarchicalPlan.from_payload(
            {
                "goal": "bad",
                "plan": [
                    {"step_id": "s1", "description": "A", "depends_on": ["missing"]},
                ],
            }
        )


def test_mark_failed_rolls_up():
    plan = HierarchicalPlan.from_flat_steps(
        "g",
        [
            {"step_id": "s1", "description": "A"},
            {"step_id": "s2", "description": "B", "depends_on": ["s1"]},
        ],
    )
    plan.mark_failed("s1", notes="tool blew up")
    assert plan.nodes["s1"].status == "failed"
    assert plan.nodes["s1"].notes
    # s2 stays pending (deps not satisfied)
    assert plan.nodes["s2"].status == "pending"


def test_context_block_and_todo_md():
    plan = HierarchicalPlan.from_flat_steps(
        "Ship feature",
        [
            {"step_id": "s1", "description": "Design", "tool_name": "file_write"},
            {"step_id": "s2", "description": "Implement", "depends_on": ["s1"]},
        ],
        success_conditions=["Tests pass"],
    )
    block = plan.to_context_block()
    assert "Ship feature" in block
    assert "s1" in block
    assert "Tests pass" in block
    todo = plan.to_todo_md()
    assert "- [ ] s1:" in todo
    plan.mark_done("s1")
    todo2 = plan.to_todo_md()
    assert "- [x] s1:" in todo2


def test_roundtrip_dict():
    plan = HierarchicalPlan.from_flat_steps(
        "g",
        [{"step_id": "s1", "description": "A"}],
    )
    plan.mark_running("s1")
    data = plan.to_dict()
    restored = HierarchicalPlan.from_dict(data)
    assert restored.goal == "g"
    assert restored.nodes["s1"].status == "running"


def test_skip_unblocks_dependents():
    plan = HierarchicalPlan.from_flat_steps(
        "g",
        [
            {"step_id": "s1", "description": "Optional"},
            {"step_id": "s2", "description": "After", "depends_on": ["s1"]},
        ],
    )
    plan.mark_skipped("s1")
    assert [r.id for r in plan.ready_steps()] == ["s2"]
