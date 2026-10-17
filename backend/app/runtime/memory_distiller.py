"""Phase 8 — Memory distiller.

Turns a finished run (goal + journal + finish summary) into durable memory
records the agent can retrieve later. Deterministic extraction — no LLM
required for the core path.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

MemoryKind = Literal["fact", "procedure", "preference", "open_question"]

_URL_RE = re.compile(r"https?://\S+", re.I)


@dataclass
class MemoryRecord:
    id: str
    kind: MemoryKind
    text: str
    source_run_id: str
    evidence_urls: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.5
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DistillResult:
    run_id: str
    goal: str
    records: list[MemoryRecord]
    skipped: int = 0

    @property
    def count(self) -> int:
        return len(self.records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "records": [r.to_dict() for r in self.records],
            "skipped": self.skipped,
            "count": len(self.records),
        }


def _mid(kind: str, text: str, run_id: str) -> str:
    h = hashlib.sha256(f"{kind}|{run_id}|{text}".encode("utf-8", "replace")).hexdigest()[:16]
    return f"mem_{kind[:3]}_{h}"


def _urls_in(text: str) -> list[str]:
    return list(dict.fromkeys(_URL_RE.findall(text or "")))


def distill_run(
    *,
    run_id: str,
    goal: str,
    finish_summary: str = "",
    journal_events: list[dict[str, Any]] | None = None,
    key_results: list[dict[str, Any]] | None = None,
    verifier: dict[str, Any] | None = None,
    max_records: int = 20,
) -> DistillResult:
    records: list[MemoryRecord] = []
    skipped = 0
    seen: set[str] = set()

    def _add(
        kind: MemoryKind,
        text: str,
        urls: list[str] | None = None,
        tags: list[str] | None = None,
        confidence: float = 0.5,
    ) -> None:
        nonlocal skipped
        text = (text or "").strip()
        if len(text) < 20:
            skipped += 1
            return
        key = f"{kind}:{text[:120].lower()}"
        if key in seen:
            skipped += 1
            return
        seen.add(key)
        records.append(
            MemoryRecord(
                id=_mid(kind, text, run_id),
                kind=kind,
                text=text[:800],
                source_run_id=run_id,
                evidence_urls=list(urls or [])[:8],
                tags=list(tags or []),
                confidence=max(0.0, min(1.0, confidence)),
            )
        )

    g = (goal or "").lower()
    for phrase, tag in (
        ("must not", "constraint"),
        ("do not", "constraint"),
        ("always", "preference"),
        ("prefer", "preference"),
        ("never", "constraint"),
    ):
        if phrase in g:
            _add("preference", goal[:400], tags=[tag, "from_goal"], confidence=0.7)
            break

    for line in (finish_summary or "").splitlines():
        line = line.strip(" -*\t")
        if len(line) >= 30 and not line.endswith("?"):
            _add("fact", line, urls=_urls_in(line), tags=["finish_summary"], confidence=0.6)

    tool_seq: list[str] = []
    for ev in journal_events or []:
        if not isinstance(ev, dict):
            continue
        et = str(ev.get("type") or ev.get("event_type") or "")
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        if et == "tool_call_result":
            name = str(payload.get("name") or "")
            result = payload.get("result")
            ok = True
            if isinstance(result, dict) and (result.get("error_code") or result.get("ok") is False):
                ok = False
            if name and ok:
                tool_seq.append(name)
    if len(tool_seq) >= 2:
        seq = " → ".join(tool_seq[:8])
        _add("procedure", f"Successful tool sequence for goal: {seq}", tags=["tool_sequence"], confidence=0.55)

    for kr in key_results or []:
        if not isinstance(kr, dict):
            continue
        snippet = str(kr.get("summary") or kr.get("snippet") or kr.get("message") or "")
        if len(snippet) >= 30:
            _add("fact", snippet, urls=_urls_in(str(kr)), tags=["key_result"], confidence=0.55)

    if isinstance(verifier, dict):
        for m in list(verifier.get("missing_evidence") or [])[:5]:
            _add("open_question", f"Missing evidence: {m}", tags=["verifier"], confidence=0.8)
        for u in list(verifier.get("unmet_requirements") or [])[:5]:
            _add("open_question", f"Unmet: {u}", tags=["verifier"], confidence=0.8)

    return DistillResult(
        run_id=run_id, goal=goal or "", records=records[:max_records], skipped=skipped
    )


class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._by_id: dict[str, MemoryRecord] = {}
        self._by_run: dict[str, list[str]] = {}

    def upsert_many(self, records: list[MemoryRecord]) -> int:
        n = 0
        for r in records:
            self._by_id[r.id] = r
            self._by_run.setdefault(r.source_run_id, []).append(r.id)
            n += 1
        return n

    def search(self, query: str, *, limit: int = 10) -> list[MemoryRecord]:
        q = {t for t in re.findall(r"[a-z0-9]{3,}", (query or "").lower())}
        scored: list[tuple[float, MemoryRecord]] = []
        for r in self._by_id.values():
            tokens = {t for t in re.findall(r"[a-z0-9]{3,}", r.text.lower())}
            score = (len(q & tokens) / max(1, len(q))) if q else 0.0
            if score > 0:
                scored.append((score * r.confidence, r))
        scored.sort(key=lambda x: -x[0])
        return [r for _, r in scored[:limit]]

    def for_run(self, run_id: str) -> list[MemoryRecord]:
        return [self._by_id[i] for i in self._by_run.get(run_id, []) if i in self._by_id]


__all__ = ["MemoryRecord", "DistillResult", "distill_run", "InMemoryMemoryStore"]
