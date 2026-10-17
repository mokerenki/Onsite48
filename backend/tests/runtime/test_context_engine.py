"""Phase 3: context engineering unit tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.runtime.context_engine import (
    ContextEngine,
    estimate_tokens,
    extractive_summary,
)


def _msg(role: str, content: str, **kwargs):
    d = {"role": role, "content": content}
    d.update(kwargs)
    return d


def test_estimate_tokens_string_and_messages():
    assert estimate_tokens("abcd") == 1  # 4 chars → 1 token
    assert estimate_tokens([_msg("user", "x" * 40)]) >= 10
    assert estimate_tokens(None) == 0


def test_extractive_summary_prefers_tool_outcomes():
    history = [
        _msg("assistant", "I will search."),
        _msg(
            "tool",
            json.dumps({"observation": True, "tool": "web_search", "outcome": "ok", "summary": "Found 3 results"}),
            name="web_search",
        ),
        _msg("user", "Goal:\nshould be skipped in summary"),
    ]
    text = extractive_summary(history)
    assert "Prior context" in text
    assert "web_search" in text or "outcome=ok" in text
    assert "Goal:" not in text or "should be skipped" not in text


def test_pack_keeps_system_and_goal():
    engine = ContextEngine(budget_tokens=5000, recent_messages=4)
    pack = engine.pack(
        system="You are a helpful agent.",
        goal="Ship the feature",
        history=[_msg("assistant", "ok"), _msg("tool", "done", name="noop")],
    )
    assert pack.messages[0]["role"] == "system"
    assert "Ship the feature" in pack.messages[1]["content"]
    assert pack.tokens_estimated > 0
    assert pack.sections["system"] == 1


def test_pack_summarizes_older_history():
    history = [_msg("user", f"turn {i} " + ("x" * 20)) for i in range(20)]
    engine = ContextEngine(budget_tokens=8000, recent_messages=4)
    pack = engine.pack(system="sys", goal="g", history=history)
    assert pack.compacted is True
    assert pack.summarized_count == 16  # 20 - 4 recent
    # Summary block present
    contents = [str(m.get("content") or "") for m in pack.messages]
    assert any("Prior context" in c for c in contents)


def test_pack_includes_plan_block():
    engine = ContextEngine(budget_tokens=5000, recent_messages=4)
    pack = engine.pack(
        system="sys",
        goal="g",
        history=[],
        plan_block="# Plan: do the thing\n[*] s1: start",
    )
    assert pack.sections["plan"] == 1
    assert any("# Plan:" in str(m.get("content") or "") for m in pack.messages)


def test_protected_errors_survive_compaction():
    # Build a long history with an error in the middle of the older section
    history = []
    for i in range(15):
        history.append(_msg("user", f"noise {i} " + ("y" * 50)))
    history.insert(
        2,
        _msg(
            "tool",
            json.dumps({"outcome": "error", "error_code": "tool_error", "summary": "boom"}),
            name="shell",
        ),
    )
    engine = ContextEngine(budget_tokens=6000, recent_messages=3)
    pack = engine.pack(system="sys", goal="g", history=history)
    blob = json.dumps(pack.messages)
    assert "tool_error" in blob or "error" in blob


def test_over_budget_hard_trim():
    huge = "Z" * 50000
    history = [_msg("user", huge) for _ in range(5)]
    engine = ContextEngine(budget_tokens=500, recent_messages=5)
    pack = engine.pack(system="sys", goal="g", history=history)
    assert pack.tokens_estimated <= engine.budget_tokens + 50  # small slack for estimation
    assert pack.compacted is True


def test_diagnostics_shape():
    engine = ContextEngine(budget_tokens=3000, recent_messages=4)
    pack = engine.pack(system="s", goal="g", history=[_msg("assistant", "hi")])
    d = pack.to_diagnostics()
    assert "tokens_estimated" in d
    assert "budget_tokens" in d
    assert d["message_count"] == len(pack.messages)
