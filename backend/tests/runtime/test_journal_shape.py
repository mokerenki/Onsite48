"""Phase C: journal events are dict-shaped; verifier does not crash."""
from __future__ import annotations
import asyncio
import pytest

from app.runtime.journal import Journal
from app.runtime.verifier import _build_evidence_ledger


@pytest.mark.asyncio
async def test_list_events_returns_dicts():
    j = Journal(session_factory=None)
    rid = "run-test-1"
    await j.append(rid, "run_started", {"goal": "g"})
    await j.append(rid, "tool_call_result", {"ok": True})
    events = await j.list_events(rid)
    assert isinstance(events, list)
    assert all(isinstance(e, dict) for e in events)
    assert events[0]["type"] == "run_started"
    assert events[0]["event_type"] == "run_started"
    assert "payload" in events[0]
    assert events[1]["type"] == "tool_call_result"


@pytest.mark.asyncio
async def test_replay_from_dicts():
    j = Journal(session_factory=None)
    rid = "run-test-2"
    await j.append(rid, "run_started", {"goal": "hello", "user_id": "u1"})
    await j.append(rid, "model_response", {"tokens": 5, "cost_usd": 0.01})
    await j.append(rid, "run_finished", {"status": "completed", "summary": "done"})
    state = await j.replay(rid, goal="hello")
    assert state.goal == "hello"
    assert state.status == "finished"
    assert state.token_count == 5
    assert abs(state.cost_usd - 0.01) < 1e-9


def test_verifier_reads_type_or_event_type():
    events = [
        {"type": "tool_call_result", "payload": {"name": "x"}},
        {"event_type": "run_finished", "payload": {"summary": "ok"}},
        {"type": "model_response", "payload": {"tokens": 1}},  # not high-signal: must be filtered out
    ]
    ledger = _build_evidence_ledger(
        goal="g",
        finish_summary="s",
        finish_status="completed",
        journal_events=events,
    )
    assert "JOURNAL_EXCERPT" in ledger
    assert "tool_call_result" in ledger
    assert "run_finished" in ledger
    assert "model_response" not in ledger
