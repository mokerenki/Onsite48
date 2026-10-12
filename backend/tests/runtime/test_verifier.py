"""Unit tests for independent completion verifier helpers."""
from app.runtime.verifier import (
    apply_verdict_to_terminal,
    user_facing_message,
    _parse_verdict,
    TERMINAL_STATES,
)


def test_parse_verdict_pass():
    v = _parse_verdict('{"verdict": "pass", "unmet_requirements": [], "missing_evidence": []}')
    assert v["verdict"] == "pass"


def test_parse_verdict_markdown_fence():
    v = _parse_verdict('```json\n{"verdict": "fail", "unmet_requirements": ["email not sent"], "missing_evidence": []}\n```')
    assert v["verdict"] == "fail"
    assert "email not sent" in v["unmet_requirements"]


def test_parse_verdict_garbage():
    v = _parse_verdict("not json at all")
    assert v["verdict"] == "unverifiable"


def test_apply_pass():
    t = apply_verdict_to_terminal("completed", {"verdict": "pass"}, "done")
    assert t["status"] == "completed"


def test_apply_fail_retry():
    t = apply_verdict_to_terminal(
        "completed",
        {"verdict": "fail", "unmet_requirements": ["x"], "missing_evidence": []},
        "done",
        finish_attempts=1,
    )
    assert t["status"] == "_retry_finish"


def test_apply_fail_final():
    t = apply_verdict_to_terminal(
        "completed",
        {"verdict": "fail", "unmet_requirements": ["x"], "missing_evidence": []},
        "done",
        finish_attempts=99,
    )
    assert t["status"] == "failed"


def test_apply_unverifiable():
    t = apply_verdict_to_terminal(
        "completed",
        {"verdict": "unverifiable", "missing_evidence": ["no readback"]},
        "done",
    )
    assert t["status"] == "completed_unverified"
    assert "Could not fully verify" in t["summary"] or "Unverified" in user_facing_message(
        t["status"], t["summary"]
    )


def test_terminal_states_complete():
    assert "completed" in TERMINAL_STATES
    assert "completed_unverified" in TERMINAL_STATES
    assert "waiting_approval" in TERMINAL_STATES
    assert "needs_input" in TERMINAL_STATES


def test_user_facing_prefix():
    assert user_facing_message("failed", "oops").startswith("[Failed]")
    assert user_facing_message("completed", "ok") == "ok"
