"""Phase 1: observation loop unit tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.runtime.observation import (
    ObservationTracker,
    format_observation_message,
    observe_tool_result,
)


def test_observe_ok_result():
    obs = observe_tool_result(
        tool="web_search",
        call_id="c1",
        result={"ok": True, "results": [{"url": "https://example.com", "title": "Ex"}]},
    )
    assert obs.outcome == "ok"
    assert obs.tool == "web_search"
    assert "example.com" in obs.evidence_urls[0]
    assert obs.fingerprint
    assert obs.error_code is None


def test_observe_timeout():
    obs = observe_tool_result(
        tool="sleep",
        call_id="c2",
        result={"error_code": "timeout", "message": "exceeded 30s", "retryable": True},
    )
    assert obs.outcome == "timeout"
    assert obs.retryable is True
    assert obs.error_code == "timeout"


def test_observe_error():
    obs = observe_tool_result(
        tool="shell_exec",
        call_id="c3",
        result={"error_code": "tool_error", "message": "boom", "retryable": False},
    )
    assert obs.outcome == "error"
    assert obs.retryable is False


def test_observe_needs_input():
    obs = observe_tool_result(
        tool="gmail_send",
        call_id="c4",
        result={"status": "needs_input", "message": "reconnect required"},
    )
    assert obs.outcome == "needs_input"


def test_tracker_detects_stagnation():
    tracker = ObservationTracker(repeat_limit=3)
    r = {"ok": True, "output": "same"}
    for i in range(3):
        obs = observe_tool_result(tool="noop", call_id=f"c{i}", result=r)
        diag = tracker.record(obs)
    assert diag["stagnant"] is True
    assert diag["repeat_count"] >= 3


def test_tracker_resets_on_progress():
    tracker = ObservationTracker(repeat_limit=3)
    for i, out in enumerate(["a", "b", "c"]):
        obs = observe_tool_result(tool="noop", call_id=f"c{i}", result={"output": out})
        diag = tracker.record(obs)
    assert diag["stagnant"] is False


def test_format_observation_message_json():
    obs = observe_tool_result(
        tool="web_search",
        call_id="call_9",
        result={"ok": True, "message": "found 3 results", "results": []},
    )
    msg = format_observation_message(obs, untrusted=False)
    assert msg["role"] == "tool"
    assert msg["tool_call_id"] == "call_9"
    assert msg["name"] == "web_search"
    body = json.loads(msg["content"])
    assert body["observation"] is True
    assert body["outcome"] == "ok"
    assert "summary" in body


def test_fingerprint_stable():
    r = {"ok": True, "x": 1}
    a = observe_tool_result(tool="t", call_id="1", result=r)
    b = observe_tool_result(tool="t", call_id="2", result=r)
    assert a.fingerprint == b.fingerprint
