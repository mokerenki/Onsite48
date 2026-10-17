"""Phase 7 — Claim grounding (evidence-backed claims).

Deterministic pre-check used alongside the model-backed completion verifier.

Extracts atomic claims from a finish summary and scores whether each claim is
grounded in evidence drawn from the journal / tool results / URLs.

This does not replace `verify_completion`; it supplies structured claim-level
signal the loop and auditors can trust without an LLM.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# Sentences that are not load-bearing claims.
_HEDGE_PREFIXES = (
    "i think",
    "maybe",
    "perhaps",
    "it seems",
    "note that",
    "please",
    "sorry",
)

_URL_RE = re.compile(r"https?://[^\s)\]>"]+", re.I)
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
    coverage: float  # grounded / total, or 1.0 if no claims
    verdict: str  # pass | fail | unverifiable
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
    """Split a finish summary into atomic claim-like sentences."""
    if not summary or not str(summary).strip():
        return []
    text = str(summary).strip()
    # Prefer line bullets
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
        # Skip pure questions
        if c.endswith("?"):
            continue
        claims.append(c[:400])
        if len(claims) >= max_claims:
            break
    return claims


def _evidence_corpus(
    *,
    journal_events: list[dict[str, Any]] | None = None,
    key_results: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    extra_urls: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Build a lowercase text corpus and URL list from evidence sources."""
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

    corpus = "\n".join(chunks).lower()
    return corpus, urls


def json_safe(obj: Any) -> str:
    try:
        import json

        return json.dumps(obj, default=str)
    except Exception:
        return str(obj)


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", text.lower()) if t}


def ground_claim(claim: str, corpus: str, urls: list[str]) -> Claim:
    """Determine if a single claim is grounded in corpus/urls."""
    claim_urls = _URL_RE.findall(claim)
    matched_urls = [u for u in claim_urls if any(u.rstrip(".,;") in uu for uu in urls)]
    # URL in claim that appears in evidence
    if claim_urls and matched_urls:
        return Claim(text=claim, grounded=True, evidence_refs=matched_urls[:5], reason="url_match")

    # Overlap of content tokens with corpus
    ct = _tokens(claim)
    if not ct:
        return Claim(text=claim, grounded=False, reason="empty_tokens")

    # Direct substring of a significant noun phrase (first 8 tokens joined)
    snippet = " ".join(list(ct)[:8])
    # Better: check fraction of claim tokens present in corpus
    hits = sum(1 for t in ct if t in corpus)
    ratio = hits / max(1, len(ct))

    # Numbers in claim should appear in corpus when present
    numbers = re.findall(r"\b\d+(?:\.\d+)?%?\b", claim)
    number_ok = all(n.lower() in corpus for n in numbers) if numbers else True

    if ratio >= 0.45 and number_ok:
        refs = [u for u in urls[:3]]
        return Claim(
            text=claim,
            grounded=True,
            evidence_refs=refs,
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
    """Ground all claims in summary against evidence."""
    claims_text = extract_claims(summary)
    corpus, urls = _evidence_corpus(
        journal_events=journal_events,
        key_results=key_results,
        artifacts=artifacts,
        extra_urls=evidence_urls,
    )

    if not claims_text:
        # No extractable claims — treat as unverifiable rather than pass
        return ClaimReport(
            claims=[],
            grounded_count=0,
            ungrounded_count=0,
            coverage=1.0,
            verdict="unverifiable",
            notes="no_claims_extracted",
        )

    grounded: list[Claim] = []
    for c in claims_text:
        grounded.append(ground_claim(c, corpus, urls))

    ok = sum(1 for c in grounded if c.grounded)
    bad = len(grounded) - ok
    coverage = ok / len(grounded)

    if not corpus and not urls:
        verdict = "unverifiable"
        notes = "no_evidence_corpus"
    elif coverage >= min_coverage and bad == 0:
        verdict = "pass"
        notes = "all_claims_grounded"
    elif coverage >= min_coverage:
        verdict = "pass"
        notes = "coverage_threshold_met"
    else:
        verdict = "fail"
        notes = "coverage_below_threshold"

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
    """Attach claim report; downgrade model verdict when claims fail hard."""
    out = dict(verdict or {})
    out["claim_grounding"] = report.to_dict()

    # Only escalate severity — never upgrade fail → pass via claims alone
    model_v = str(out.get("verdict") or "unverifiable")
    if report.verdict == "fail" and model_v == "pass":
        out["verdict"] = "fail"
        missing = list(out.get("missing_evidence") or [])
        for c in report.claims:
            if not c.grounded:
                missing.append(f"ungrounded_claim: {c.text[:120]}")
        out["missing_evidence"] = missing[:20]
        notes = str(out.get("notes") or "")
        out["notes"] = (notes + " | claim_grounding_failed").strip(" |")
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
