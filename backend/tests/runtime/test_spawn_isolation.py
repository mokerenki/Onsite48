"""Phase 5: isolation, cancel, cost rollup, locks, wide_map partial failure."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.runtime.roles_v2 import intersect_allowlist, LEAD
from app.runtime.locks import ResourceLockManager, ResourceBusy, resources_for_tool
from app.runtime.spawn import SpawnManager, wide_map


def test_worker_never_sees_lead_secret(tmp_path: Path):
    secret = "LEAD_SECRET_TOKEN_9f3a"
    seen_goals: list[str] = []

    async def fake_loop(**kwargs):
        goal = kwargs.get("goal") or ""
        seen_goals.append(goal)
        assert "messages" not in kwargs or not kwargs.get("messages")
        assert secret not in goal
        return {
            "status": "completed",
            "result": {"summary": "ok", "status": "completed"},
            "cost_usd": 0.02,
            "state": {"cost_usd": 0.02},
        }

    async def body():
        mgr = SpawnManager(
            parent_run_id="lead-1",
            parent_role=LEAD,
            workspace_root=tmp_path,
            loop_runner=fake_loop,
            depth=0,
        )
        h = await mgr.spawn_agent(
            role="researcher",
            goal="Research topic X; do not mention any secrets.",
            inputs=["public input only"],
        )
        await mgr.await_agents([h])
        assert h.status == "completed"
        assert len(seen_goals) == 1
        assert secret not in seen_goals[0]

    asyncio.run(body())


def test_cancel_propagates_to_children(tmp_path: Path):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_loop(**kwargs):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return {"status": "completed", "result": {}, "state": {"cost_usd": 0}}

    async def body():
        cancel_ev = asyncio.Event()
        mgr = SpawnManager(
            parent_run_id="lead-2",
            parent_role=LEAD,
            workspace_root=tmp_path,
            loop_runner=slow_loop,
            cancel_event=cancel_ev,
        )
        h = await mgr.spawn_agent(role="coder", goal="long job")
        await started.wait()
        await mgr.cancel_all()
        await asyncio.sleep(0.15)
        assert h.status == "cancelled"
        assert cancelled.is_set() or (h.task and (h.task.cancelled() or h.task.done()))

    asyncio.run(body())


def test_cost_rollup_within_one_percent(tmp_path: Path):
    costs = [0.11, 0.22, 0.33]

    async def priced_loop(**kwargs):
        c = costs.pop(0) if costs else 0.01
        return {
            "status": "completed",
            "result": {"summary": "ok", "status": "completed"},
            "state": {"cost_usd": c},
            "cost_usd": c,
        }

    async def body():
        mgr = SpawnManager(
            parent_run_id="lead-3",
            parent_role=LEAD,
            workspace_root=tmp_path,
            loop_runner=priced_loop,
        )
        hs = await mgr.spawn_agents(
            [
                {"role": "researcher", "goal": "a"},
                {"role": "researcher", "goal": "b"},
                {"role": "researcher", "goal": "c"},
            ]
        )
        await mgr.await_agents(hs)
        total = mgr.total_child_cost()
        expected = 0.11 + 0.22 + 0.33
        assert abs(total - expected) / expected < 0.01

    asyncio.run(body())


def test_resource_lock_contention():
    async def body():
        mgr = ResourceLockManager(default_ttl_s=5.0)
        h1 = await mgr.acquire("gmail:draft:42", holder_id="w1", wait_s=0.5)
        assert h1 == "w1"
        with pytest.raises(ResourceBusy):
            await mgr.acquire("gmail:draft:42", holder_id="w2", wait_s=0.3)
        await mgr.release("gmail:draft:42", "w1")
        h2 = await mgr.acquire("gmail:draft:42", holder_id="w2", wait_s=0.5)
        assert h2 == "w2"
        await mgr.release("gmail:draft:42", "w2")

    asyncio.run(body())


def test_resources_for_tool_keys():
    assert resources_for_tool("email_send", {"draft_id": "d1"}) == ["gmail:draft:d1"]
    assert resources_for_tool("calendar_create", {"event_id": "e9"}) == ["calendar:event:e9"]
    assert resources_for_tool("file_read", {"path": "x"}) == []
    keys = resources_for_tool(
        "browser", {"action": "click", "workspace_id": "w", "tab_id": "t"}
    )
    assert keys == ["browser:workspace:w:tab:t"]


def test_wide_map_partial_failure(tmp_path: Path):
    async def flaky_loop(**kwargs):
        goal = kwargs.get("goal") or ""
        if "FAIL" in goal:
            return {
                "status": "failed",
                "result": {"summary": "boom", "status": "failed"},
                "state": {"cost_usd": 0.01},
            }
        return {
            "status": "completed",
            "result": {"summary": "ok", "status": "completed"},
            "state": {"cost_usd": 0.02},
        }

    async def body():
        items = ["ok-1", "FAIL-me", "ok-2", "ok-3"]
        out = await wide_map(
            items=items,
            role="researcher",
            instruction_template="Process: {item}",
            concurrency=2,
            parent_run_id="lead-wm",
            parent_role=LEAD,
            workspace_root=tmp_path,
            loop_runner=flaky_loop,
            max_retries=0,
            cost_approval_threshold=100.0,
        )
        assert out["status"] == "partial"
        assert out["n_ok"] == 3
        assert len(out["failed_items"]) == 1
        assert Path(out["table_path"]).exists()
        assert Path(out["csv_path"]).exists()

    asyncio.run(body())


def test_depth_cap_blocks_nested_spawn(tmp_path: Path):
    async def body():
        # v2: LEAD has depth_cap=2, so depth=1 is allowed, depth=2 should fail
        mgr = SpawnManager(
            parent_run_id="lead-d",
            parent_role=LEAD,
            workspace_root=tmp_path,
            loop_runner=None,
            depth=2,  # at cap (depth_cap=2), next spawn should fail
        )
        with pytest.raises(RuntimeError, match="depth_cap"):
            await mgr.spawn_agent(role="researcher", goal="nested")

    asyncio.run(body())


def test_policy_inheritance_allowlist():
    parent = ("web_search", "file_read", "finish")
    child = ("web_search", "browser", "finish")
    inter = intersect_allowlist(parent, child)
    assert set(inter) == {"web_search", "finish"}


def test_worker_crash_does_not_corrupt_lead(tmp_path: Path):
    calls = {"n": 0}

    async def selective_loop(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("worker exploded")
        return {
            "status": "completed",
            "result": {"summary": "ok", "status": "completed"},
            "state": {"cost_usd": 0.01},
        }

    async def body():
        mgr = SpawnManager(
            parent_run_id="lead-crash",
            parent_role=LEAD,
            workspace_root=tmp_path,
            loop_runner=selective_loop,
        )
        h = await mgr.spawn_agent(role="coder", goal="will crash")
        await mgr.await_agents([h])
        assert h.status == "failed"
        assert "exploded" in (h.error or "")
        h2 = await mgr.spawn_agent(role="researcher", goal="still works")
        await mgr.await_agents([h2])
        assert h2.status == "completed"
        # Manager state intact
        assert len(mgr._handles) == 2

    asyncio.run(body())
