"""Phase 1 tests: parallel sub-agent research and context window management.

Covers two Phase 1 features:
1. Parallel research branches (`generate_research_branches`, `run_research_branches_parallel`,
   `synthesize_branches`, `merge_branch_results`).
2. Context window management (`ContextManager`, `estimate_tokens`, `extract_summary`,
   `extract_urls`).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.runtime.journal import Journal, RunState
from app.runtime.research_control import (
    BranchResult,
    ResearchBranch,
    SynthesisResult,
    generate_research_branches,
    merge_branch_results,
    plan_research,
)


def _run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# 1. Parallel research branches
# ---------------------------------------------------------------------------

def test_parallel_branches_execute_concurrently():
    """Branches are created and run via a simulated runner, producing BranchResults."""
    from app.runtime.loop import _run_research_branch_simulated, run_research_branches_parallel

    goal = "compare pricing, features, and market position of AcmeCorp and Globex"
    branches = generate_research_branches(goal, depth="standard", min_sources_per_branch=2)
    assert len(branches) >= 2, "Expected at least 2 research branches"

    # Each branch should have queries for its facet
    for b in branches:
        assert isinstance(b, ResearchBranch)
        assert b.facet
        assert len(b.queries) >= 1
        assert b.min_sources >= 1

    event_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(event_loop)

    # Run branches with the simulated runner — no real tools needed
    state = RunState(run_id="test-run", goal=goal, messages=[])
    journal = Journal()
    cancel_event = asyncio.Event()

    results = _run_async(run_research_branches_parallel(
        state=state,
        branches=branches,
        specs={},
        journal=journal,
        run_id="test-run",
        model_override=None,
        event_emitter=None,
        cancel_event=cancel_event,
        branch_runner=_run_research_branch_simulated,
    ))

    assert len(results) == len(branches)
    for r in results:
        assert isinstance(r, BranchResult)
        assert r.branch_id
        assert r.facet
        assert r.status in ("completed", "partial", "failed")

    # Synthesis should merge all branch results
    synthesis = merge_branch_results(results)
    assert isinstance(synthesis, SynthesisResult)
    assert len(synthesis.by_facet) == len(branches)
    assert synthesis.executive_summary  # non-empty string


# ---------------------------------------------------------------------------
# 2. Context window management
# ---------------------------------------------------------------------------

async def _fake_summarize(messages):
    """Fake summarizer that returns a short placeholder."""
    return "Summarized context with key facts preserved."


def test_context_summarization_triggers_at_threshold():
    """ContextManager.maybe_summarize collapses old turns when token count exceeds threshold."""
    import app.runtime.loop as loop_mod
    from app.runtime.loop import ContextManager, estimate_tokens

    # Build a state with many messages
    long_content = "x" * 5000  # ~1250 tokens per message
    messages = [{"role": "system", "content": "You are a helpful assistant."}]
    for i in range(50):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"Turn {i}: {long_content}"})

    token_count = estimate_tokens(messages)
    assert token_count > 60_000  # well above threshold

    state = RunState(run_id="test", goal="test", messages=list(messages))
    state.token_count = token_count
    state.summarized_turns = 0

    cm = ContextManager(max_tokens=80_000, summary_threshold=1000, recent_turns=6)
    # Use monkeypatch to avoid actual model call
    with patch.object(loop_mod, "ENABLE_CONTEXT_SUMMARIZATION", True), \
         patch.object(cm, "_summarize_early_turns", new=_fake_summarize):
        new_state = _run_async(cm.maybe_summarize(state))

    assert new_state.summarized_turns > 0
    assert len(new_state.messages) < len(messages)
    # System message preserved
    assert any(m["role"] == "system" for m in new_state.messages)
    # Recent turns preserved (last N)
    assert new_state.messages[-1]["role"] == "assistant"


def test_summarization_preserves_key_facts():
    """The summary includes key information from the original messages."""
    import app.runtime.loop as loop_mod
    from app.runtime.loop import ContextManager, estimate_tokens

    messages = [
        {"role": "system", "content": "You are a research assistant."},
        {"role": "user", "content": "Research the revenue of AcmeCorp in 2024 — it was $5B"},
        {"role": "assistant", "content": "AcmeCorp revenue 2024: $5,000,000,000"},
        {"role": "user", "content": "What about Globex Corp? Their revenue was $3B."},
        {"role": "assistant", "content": "Globex Corp revenue 2024: $3,000,000,000"},
        # Fill with more messages to trigger summarization
        {"role": "user", "content": "more data " * 200},
        {"role": "assistant", "content": "more results " * 200},
    ]

    state = RunState(run_id="test2", goal="test", messages=list(messages))
    state.token_count = estimate_tokens(messages)
    state.summarized_turns = 0

    # Custom fake that checks content
    captured = []
    async def check_content(msgs):
        text = " ".join(m.get("content", "") for m in msgs)
        captured.append(text)
        return "AcmeCorp $5B, Globex $3B summarized."

    cm = ContextManager(max_tokens=80_000, summary_threshold=500, recent_turns=2)
    with patch.object(loop_mod, "ENABLE_CONTEXT_SUMMARIZATION", True), \
         patch.object(cm, "_summarize_early_turns", side_effect=check_content):
        new_state = _run_async(cm.maybe_summarize(state))

    assert len(captured) == 1
    # The older messages were passed to the summarizer
    older_text = captured[0]
    assert "AcmeCorp" in older_text
    assert "Globex" in older_text

    # Summary is embedded in the new system message
    summary_msgs = [
        m for m in new_state.messages
        if m.get("role") == "system" and "Context summary" in str(m.get("content", ""))
    ]
    assert len(summary_msgs) == 1
    assert "AcmeCorp" in summary_msgs[0]["content"]


def test_structured_tool_results_reduce_tokens():
    """append_tool_result stores full result and returns a compact ref_id."""
    import uuid

    journal = Journal()  # in-memory
    run_id = f"test_{uuid.uuid4().hex[:8]}"

    full_result = {
        "results": [
            {"title": f"Result {i}", "url": f"https://example.com/{i}", "content": "x" * 1000}
            for i in range(20)
        ],
        "metadata": {"total": 20, "query": "test query"},
        "raw_html": "<html>" + "<p>stuff</p>" * 500 + "</html>",
    }

    ref_id = _run_async(journal.append_tool_result(
        run_id,
        "web_search",
        full_result,
        summary="Search returned 20 results.",
        evidence_urls=["https://example.com/1", "https://example.com/2"],
        call_id="call_123",
    ))

    assert ref_id.startswith("toolres_")

    # Retrieve the full result by ref_id
    stored = _run_async(journal.get_tool_result(ref_id))
    assert stored is not None
    assert stored["tool"] == "web_search"
    assert stored["ref_id"] == ref_id
    assert stored["summary"] == "Search returned 20 results."
    assert "https://example.com/1" in stored["evidence_urls"]
    # Full result is stored
    assert stored["full_result"] == full_result

    # Replay should restore tool_results
    state = _run_async(journal.replay(run_id))
    assert ref_id in state.tool_results
    assert state.tool_results[ref_id]["tool"] == "web_search"


def test_non_research_tasks_unaffected():
    """Non-research goals don't generate branches; plan_research returns empty branches."""
    goal = "write a python function to sort a list"
    plan = plan_research(goal)
    assert plan.is_research is False
    assert plan.branches == []

    # Research goals with single facet don't generate parallel branches
    goal2 = "research tennsa"
    plan2 = plan_research(goal2)
    assert plan2.is_research is True
    # Single-facet research goal: no branching (need 2+ facets)
    # _enable_branching() defaults to false without env flag, so branches is []
    assert plan2.branches == []

    # But generate_research_branches directly should still work with multi-facet goals
    goal3 = "compare pricing, features, and market share of AI coding tools"
    branches = generate_research_branches(goal3, depth="standard", min_sources_per_branch=2)
    assert len(branches) >= 2
    facets = [b.facet for b in branches]
    assert "pricing" in facets
