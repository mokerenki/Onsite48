"""Phase 7 — Claim grounding (evidence-backed claims).

Deterministic pre-check used alongside the model-backed completion verifier.

Extracts atomic claims from a finish summary and scores whether each claim is
grounded in evidence drawn from the journal / tool results / URLs.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

_HEDGE_PREFIXES = (
    "i think",
    "maybe",
    "perhaps",
    "it seems",
    "note that",
    "please",
    "sorry",
)

# Use single-quoted raw string so " can appear in the character class.
_URL_RE = re.compile(r'https?://[^\s)\]>"]+', re.I)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|(?<=\n)")


@dataclass
class Claim:
    text: str
    grounded: bool
    evidence_refs: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClaimReport:
    claims: list[Claim]
    grounded_count: int
    ungrounded_count: int
    coverage: float
    verdict: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "claims": [c.to_dict() for c in self.claims],
            "grounded_count": self.grounded_count,
            "ungrounded_count": self.ungrounded_count,
            "coverage": round(self.coverage, 3),
            "verdict": self.verdict,
            "notes": self.notes,
        }


def extract_claims(summary: str, *, max_claims: int = 12) -> list[str]:
    if not summary or not str(summary).strip():
        return []
    text = str(summary).strip()
    lines = [ln.strip(" -*\t") for ln in text.splitlines() if ln.strip()]
    candidates: list[str] = []
    if len(lines) > 1:
        for ln in lines:
            if len(ln) >= 20:
                candidates.append(ln)
    if not candidates:
        parts = _SENTENCE_RE.split(text)
        candidates = [p.strip() for p in parts if p and len(p.strip()) >= 20]

    claims: list[str] = []
    for c in candidates:
        low = c.lower()
        if any(low.startswith(h) for h in _HEDGE_PREFIXES):
            continue
        if c.endswith("?"):
            continue
        claims.append(c[:400])
        if len(claims) >= max_claims:
            break
    return claims


def json_safe(obj: Any) -> str:
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return str(obj)


def _evidence_corpus(
    *,
    journal_events: list[dict[str, Any]] | None = None,
    key_results: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    extra_urls: list[str] | None = None,
) -> tuple[str, list[str]]:
    chunks: list[str] = []
    urls: list[str] = []

    def _add_url(u: str) -> None:
        if u and u not in urls:
            urls.append(u)

    for u in extra_urls or []:
        _add_url(u)

    for ev in journal_events or []:
        if not isinstance(ev, dict):
            continue
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else ev
        et = str(ev.get("type") or ev.get("event_type") or "")
        if et in ("tool_call_result", "tool_result", "readback_result", "branch_result"):
            chunks.append(json_safe(payload)[:2000])
            for m in _URL_RE.findall(json_safe(payload)):
                _add_url(m.rstrip(".,;"))
        if isinstance(payload, dict):
            for key in ("url", "source_url", "href"):
                v = payload.get(key)
                if isinstance(v, str) and v.startswith("http"):
                    _add_url(v)

    for kr in key_results or []:
        if isinstance(kr, dict):
            chunks.append(json_safe(kr)[:1000])
            for m in _URL_RE.findall(json_safe(kr)):
                _add_url(m.rstrip(".,;"))

    for art in artifacts or []:
        if isinstance(art, dict):
            chunks.append(json_safe(art)[:500])
            p = art.get("path") or art.get("name")
            if isinstance(p, str):
                chunks.append(p.lower())

    return "\n".join(chunks).lower(), urls


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", text.lower()) if t}


def ground_claim(claim: str, corpus: str, urls: list[str]) -> Claim:
    claim_urls = _URL_RE.findall(claim)
    matched_urls = [u for u in claim_urls if any(u.rstrip(".,;") in uu for uu in urls)]
    if claim_urls and matched_urls:
        return Claim(text=claim, grounded=True, evidence_refs=matched_urls[:5], reason="url_match")

    ct = _tokens(claim)
    if not ct:
        return Claim(text=claim, grounded=False, reason="empty_tokens")

    hits = sum(1 for t in ct if t in corpus)
    ratio = hits / max(1, len(ct))

    numbers = re.findall(r"\b\d+(?:\.\d+)?%?\b", claim)
    number_ok = all(n.lower() in corpus for n in numbers) if numbers else True

    if ratio >= 0.45 and number_ok:
        return Claim(
            text=claim,
            grounded=True,
            evidence_refs=urls[:3],
            reason=f"token_overlap:{ratio:.2f}",
        )

    if numbers and not number_ok:
        return Claim(text=claim, grounded=False, reason="numeric_mismatch")

    return Claim(
        text=claim,
        grounded=False,
        reason=f"insufficient_overlap:{ratio:.2f}",
    )


def ground_claims(
    summary: str,
    *,
    journal_events: list[dict[str, Any]] | None = None,
    key_results: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    evidence_urls: list[str] | None = None,
    min_coverage: float = 0.5,
) -> ClaimReport:
    claims_text = extract_claims(summary)
    corpus, urls = _evidence_corpus(
        journal_events=journal_events,
        key_results=key_results,
        artifacts=artifacts,
        extra_urls=evidence_urls,
    )

    if not claims_text:
        return ClaimReport(
            claims=[],
            grounded_count=0,
            ungrounded_count=0,
            coverage=1.0,
            verdict="unverifiable",
            notes="no_claims_extracted",
        )

    grounded = [ground_claim(c, corpus, urls) for c in claims_text]
    ok = sum(1 for c in grounded if c.grounded)
    bad = len(grounded) - ok
    coverage = ok / len(grounded)

    if not corpus and not urls:
        verdict, notes = "unverifiable", "no_evidence_corpus"
    elif coverage >= min_coverage and bad == 0:
        verdict, notes = "pass", "all_claims_grounded"
    elif coverage >= min_coverage:
        verdict, notes = "pass", "coverage_threshold_met"
    else:
        verdict, notes = "fail", "coverage_below_threshold"

    return ClaimReport(
        claims=grounded,
        grounded_count=ok,
        ungrounded_count=bad,
        coverage=coverage,
        verdict=verdict,
        notes=notes,
    )


def merge_claim_report_into_verdict(
    verdict: dict[str, Any],
    report: ClaimReport,
) -> dict[str, Any]:
    out = dict(verdict or {})
    out["claim_grounding"] = report.to_dict()
    model_v = str(out.get("verdict") or "unverifiable")
    if report.verdict == "fail" and model_v == "pass":
        out["verdict"] = "fail"
        missing = list(out.get("missing_evidence") or [])
        for c in report.claims:
            if not c.grounded:
                missing.append(f"ungrounded_claim: {c.text[:120]}")
        out["missing_evidence"] = missing[:20]
        out["notes"] = (str(out.get("notes") or "") + " | claim_grounding_failed").strip(" |")
    elif report.verdict == "unverifiable" and model_v == "pass" and report.notes == "no_evidence_corpus":
        out["verdict"] = "unverifiable"
        out["notes"] = (str(out.get("notes") or "") + " | no_evidence_for_claims").strip(" |")
    return out


__all__ = [
    "Claim",
    "ClaimReport",
    "extract_claims",
    "ground_claim",
    "ground_claims",
    "merge_claim_report_into_verdict",
]
