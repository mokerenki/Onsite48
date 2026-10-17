"""Phase 5: browser quality unit tests."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.runtime.browser.quality import (
    classify_receipt,
    compact_observation,
    enrich_action_result,
    enrich_observe_result,
    score_observation,
)


def _obs(**kwargs):
    base = {
        "observation_id": "obs_1",
        "revision": 1,
        "url": "https://example.com",
        "title": "Example",
        "frames": [{"frame_id": "main", "url": "https://example.com", "name": ""}],
        "elements": [
            {"ref": "e1", "role": "button", "name": "Submit", "tag": "button", "fingerprint": "fp1"},
            {"ref": "e2", "role": "link", "name": "Home", "tag": "a", "fingerprint": "fp2"},
            {"ref": "e3", "role": "textbox", "name": "Email", "tag": "input", "type": "email", "fingerprint": "fp3"},
            {"ref": "e4", "role": "text", "name": "Footer", "tag": "span", "fingerprint": "fp4"},
        ],
    }
    base.update(kwargs)
    return base


def test_score_rich_page_high():
    q = score_observation(_obs())
    assert q.score >= 0.5
    assert q.interactive_count >= 3
    assert q.has_url and q.has_title
    assert not q.is_low_quality


def test_score_blank_page_low():
    q = score_observation(_obs(url="about:blank", title="", elements=[]))
    assert q.score < 0.25
    assert q.is_low_quality
    assert "blank_page" in q.reasons or "no_interactive_elements" in q.reasons


def test_compact_prefers_interactive_and_caps():
    elements = []
    for i in range(60):
        role = "button" if i < 30 else "text"
        elements.append(
            {"ref": f"e{i}", "role": role, "name": f"n{i}", "tag": "div", "fingerprint": f"f{i}"}
        )
    out = compact_observation(_obs(elements=elements), max_elements=10)
    assert out["element_count_returned"] == 10
    assert out["element_count_total"] == 60
    # First elements should be buttons (interactive)
    assert all(e["role"] == "button" for e in out["elements"][:10])
    assert "quality" in out


def test_classify_stale_receipt():
    c = classify_receipt({"stale_observation": True, "ok": False})
    assert c["kind"] == "stale"
    assert c["should_reobserve"] is True
    assert c["should_retry"] is True


def test_classify_policy_takeover():
    c = classify_receipt({"ok": False, "policy": "TAKEOVER", "reason": "password"})
    assert c["kind"] == "policy"
    assert c["should_retry"] is False


def test_classify_ok():
    c = classify_receipt({"ok": True, "stale_observation": False})
    assert c["kind"] == "ok"


def test_enrich_observe_result():
    r = enrich_observe_result(_obs())
    assert r["ok"] is True
    assert r["action"] == "observe"
    assert "observation" in r
    assert "quality" in r


def test_enrich_action_result_compacts_nested_obs():
    receipt = {
        "ok": True,
        "stale_observation": False,
        "observation": _obs(),
    }
    out = enrich_action_result(receipt)
    assert "quality" in out
    assert out["quality"]["kind"] == "ok"
    assert out["observation"]["element_count_returned"] <= out["observation"]["element_count_total"]
