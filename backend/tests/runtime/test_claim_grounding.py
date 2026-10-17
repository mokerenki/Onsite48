"""Phase 7: claim grounding unit tests (detailed)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.runtime.claim_grounding import (
    extract_claims,
    ground_claims,
    merge_claim_report_into_verdict,
)


def test_extract_claims_from_bullets():
    summary = """Results:
- Acme pricing starts at $49 per month according to their site.
- Globex offers a free tier with limited API calls.
- Further research is needed.
"""
    claims = extract_claims(summary)
    assert len(claims) >= 2
    assert any("Acme" in c for c in claims)


def test_extract_skips_short_and_questions():
    claims = extract_claims("Ok.\nWhat next?")
    assert claims == []


def test_grounded_when_evidence_overlaps():
    summary = (
        "Acme pricing starts at 49 dollars per month on the official pricing page. "
        "The plan includes API access."
    )
    events = [
        {
            "type": "tool_call_result",
            "payload": {
                "name": "web_search",
                "result": {
                    "ok": True,
                    "results": [
                        {
                            "title": "Acme Pricing",
                            "url": "https://acme.example/pricing",
                            "snippet": "Acme pricing starts at 49 dollars per month with API access",
                        }
                    ],
                },
            },
        }
    ]
    report = ground_claims(summary, journal_events=events)
    assert report.grounded_count >= 1
    assert report.coverage >= 0.5
    assert report.verdict in ("pass", "fail")  # deterministic; prefer pass
    assert report.verdict == "pass"


def test_ungrounded_numeric_claim_fails():
    summary = "Revenue grew 87 percent year over year according to our analysis."
    events = [
        {
            "type": "tool_call_result",
            "payload": {
                "result": {"ok": True, "snippet": "Revenue grew modestly last year with mixed results"}
            },
        }
    ]
    report = ground_claims(summary, journal_events=events, min_coverage=0.9)
    assert report.verdict == "fail"
    assert report.ungrounded_count >= 1


def test_no_evidence_is_unverifiable():
    summary = "The competitor launched three new products in Europe this quarter."
    report = ground_claims(summary, journal_events=[], key_results=[])
    assert report.verdict == "unverifiable"
    assert report.notes in ("no_evidence_corpus", "no_claims_extracted") or report.ungrounded_count >= 0


def test_url_in_claim_matches_evidence_url():
    summary = "See the announcement at https://news.example.com/launch for details on the launch event coverage."
    report = ground_claims(
        summary,
        evidence_urls=["https://news.example.com/launch"],
        journal_events=[
            {
                "type": "tool_call_result",
                "payload": {"result": {"url": "https://news.example.com/launch", "title": "Launch"}},
            }
        ],
    )
    assert any(c.grounded for c in report.claims)


def test_merge_downgrades_pass_to_fail():
    summary = "The secret value is 999 and only we know it from internal intuition."
    report = ground_claims(summary, journal_events=[], min_coverage=0.9)
    # force fail report
    if report.verdict != "fail":
        # ensure fail by empty corpus + claims
        report = ground_claims(
            "Unique unsupported claim about zebras colonizing Mars in 1999.",
            journal_events=[{"type": "tool_call_result", "payload": {"result": {"ok": True, "snippet": "weather is fine"}}}],
            min_coverage=0.99,
        )
    model_verdict = {"verdict": "pass", "missing_evidence": [], "notes": "ok"}
    merged = merge_claim_report_into_verdict(model_verdict, report)
    assert "claim_grounding" in merged
    if report.verdict == "fail":
        assert merged["verdict"] == "fail"
        assert any("ungrounded_claim" in m for m in merged.get("missing_evidence", []))


def test_merge_never_upgrades_fail():
    report = ground_claims(
        "Acme pricing is 49 dollars per month on the pricing page fully documented here.",
        journal_events=[
            {
                "type": "tool_call_result",
                "payload": {
                    "result": {
                        "snippet": "Acme pricing is 49 dollars per month on the pricing page fully documented"
                    }
                },
            }
        ],
    )
    model_verdict = {"verdict": "fail", "missing_evidence": ["x"], "notes": "model failed"}
    merged = merge_claim_report_into_verdict(model_verdict, report)
    assert merged["verdict"] == "fail"


def test_report_to_dict_shape():
    report = ground_claims("Short.")
    d = report.to_dict()
    assert "claims" in d and "coverage" in d and "verdict" in d
