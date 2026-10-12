"""Skills progressive disclosure and load."""
from __future__ import annotations

from app.skills.loader import list_skill_summaries, load_skill_body


def test_list_builtin_skills():
    skills = list_skill_summaries()
    names = {s["name"] for s in skills}
    assert "docx-report" in names
    assert "data-analysis" in names
    assert "web-research-method" in names
    for s in skills:
        assert s.get("description")
        assert len(s["description"]) < 300


def test_load_skill_body():
    body = load_skill_body("docx-report")
    assert body.get("error_code") is None
    assert "python-docx" in body.get("body", "") or "docx" in body.get("body", "").lower()
    assert body.get("source") == "builtin"


def test_load_missing():
    body = load_skill_body("no-such-skill-xyz")
    assert body.get("error_code") == "not_found"


def test_no_eval_id_special_cases():
    """Grep skills for eval-id overfitting."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "app" / "skills"
    bad = []
    for md in root.rglob("SKILL.md"):
        text = md.read_text(encoding="utf-8")
        for token in ("eval-", "fixture-", "task_id", "held-out", "heldout", "GAIA"):
            if token.lower() in text.lower():
                bad.append((str(md), token))
    assert bad == [], f"skills contain eval special-cases: {bad}"
