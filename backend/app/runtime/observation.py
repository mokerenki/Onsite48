"""Phase 1 — Observation loop for the agent runtime.

Every tool result becomes a structured Observation before it enters the
model context. Observations carry:

* outcome (ok / error / timeout / needs_input)
* bounded summary for the model
* evidence URLs when present
* stable fingerprint for progress detection
* optional recovery class for the recovery path

The observation loop is the contract between tool execution and the next
model turn: the model must see what happened, not a raw dump, and the
runtime must detect when consecutive observations show no progress.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Outcome = Literal["ok", "error", "timeout", "needs_input", "cancelled"]

# How many consecutive identical fingerprints before we call it no-progress.
NO_PROGRESS_REPEAT = int(os.environ.get("OBSERVATION_NO_PROGRESS_REPEAT", "3"))


@dataclass
class Observation:
    tool: str
    call_id: str
    outcome: Outcome
    summary: str
    fingerprint: str
    evidence_urls: list[str] = field(default_factory=list)
    error_code: str | None = None
    retryable: bool = False
    recovery_class: str | None = None
    elapsed_ms: float | None = None
    raw_ref: str | None = None  # journal ref when full payload is offloaded
    ts: float = field(default_factory=time.time)

    def to_model_payload(self, *, max_chars: int = 4000) -> dict[str, Any]:
        """Compact JSON the model sees as the tool message content."""
        payload: dict[str, Any] = {
            "observation": True,
            "tool": self.tool,
            "outcome": self.outcome,
            "summary": (self.summary or "")[: max_chars // 2],
        }
        if self.evidence_urls:
            payload["evidence_urls"] = self.evidence_urls[:10]
        if self.error_code:
            payload["error_code"] = self.error_code
            payload["retryable"] = self.retryable
        if self.recovery_class:
            payload["recovery_class"] = self.recovery_class
        if self.raw_ref:
            payload["raw_ref"] = self.raw_ref
        if self.elapsed_ms is not None:
            payload["elapsed_ms"] = round(self.elapsed_ms, 1)
        text = json.dumps(payload, default=str, ensure_ascii=False)
        if len(text) > max_chars:
            payload["summary"] = (self.summary or "")[: max(200, max_chars // 4)]
            text = json.dumps(payload, default=str, ensure_ascii=False)[:max_chars]
            return {"_truncated": True, "content": text}
        return payload

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fingerprint(tool: str, result: Any) -> str:
    if isinstance(result, dict):
        try:
            raw = json.dumps(result, sort_keys=True, default=str)
        except (TypeError, ValueError):
            raw = repr(result)
    else:
        raw = str(result)
    basis = f"{tool}|{raw}"
    return hashlib.sha256(basis.encode("utf-8", "replace")).hexdigest()[:16]


def _extract_urls(result: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(result, dict):
        for key in ("url", "href", "link", "source_url", "canonical_url"):
            v = result.get(key)
            if isinstance(v, str) and v.startswith("http"):
                urls.append(v)
        for key in ("results", "sources", "items", "hits", "data"):
            v = result.get(key)
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        for uk in ("url", "href", "link"):
                            uv = item.get(uk)
                            if isinstance(uv, str) and uv.startswith("http") and uv not in urls:
                                urls.append(uv)
    elif isinstance(result, str) and result.startswith("http"):
        urls.append(result)
    return urls[:20]


def _summary(result: Any, *, max_len: int = 400) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result[:max_len]
    if isinstance(result, dict):
        for key in ("message", "summary", "output", "text", "error", "error_code"):
            v = result.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()[:max_len]
        if result.get("results") is not None and isinstance(result["results"], list):
            return f"Returned {len(result['results'])} results."
        if result.get("ok") is True:
            return "Operation completed successfully."
    return str(result)[:max_len]


def _classify_outcome(result: Any) -> tuple[Outcome, str | None, bool]:
    if result is None:
        return "error", "empty_result", False
    if isinstance(result, dict):
        code = str(result.get("error_code") or "").lower()
        if code == "timeout" or "timeout" in str(result.get("message") or "").lower():
            return "timeout", code or "timeout", bool(result.get("retryable", True))
        if code in ("cancelled", "canceled") or result.get("status") == "cancelled":
            return "cancelled", code or "cancelled", False
        if code in ("needs_input", "auth_required") or result.get("status") == "needs_input":
            return "needs_input", code or "needs_input", False
        if result.get("error") or result.get("error_code") or result.get("ok") is False:
            return "error", code or "tool_error", bool(result.get("retryable", False))
        status = str(result.get("status") or "").lower()
        if status in ("error", "failed", "denied"):
            return "error", code or status, bool(result.get("retryable", False))
    return "ok", None, False


def observe_tool_result(
    *,
    tool: str,
    call_id: str,
    result: Any,
    recovery_class: str | None = None,
    elapsed_ms: float | None = None,
    raw_ref: str | None = None,
) -> Observation:
    """Build a structured Observation from a tool boundary result."""
    outcome, error_code, retryable = _classify_outcome(result)
    return Observation(
        tool=tool,
        call_id=call_id or "",
        outcome=outcome,
        summary=_summary(result),
        fingerprint=_fingerprint(tool, result),
        evidence_urls=_extract_urls(result),
        error_code=error_code,
        retryable=retryable,
        recovery_class=recovery_class,
        elapsed_ms=elapsed_ms,
        raw_ref=raw_ref,
    )


class ObservationTracker:
    """Track recent observations to detect no-progress loops."""

    def __init__(self, *, repeat_limit: int = NO_PROGRESS_REPEAT):
        self.repeat_limit = max(2, int(repeat_limit))
        self._recent: list[str] = []
        self._all: list[Observation] = []

    def record(self, obs: Observation) -> dict[str, Any]:
        """Record observation; return progress diagnostics."""
        self._all.append(obs)
        self._recent.append(obs.fingerprint)
        # Keep a sliding window
        if len(self._recent) > self.repeat_limit + 2:
            self._recent = self._recent[-(self.repeat_limit + 2) :]
        stagnant = (
            len(self._recent) >= self.repeat_limit
            and len(set(self._recent[-self.repeat_limit :])) == 1
        )
        return {
            "fingerprint": obs.fingerprint,
            "outcome": obs.outcome,
            "stagnant": stagnant,
            "repeat_count": self._recent[-self.repeat_limit :].count(obs.fingerprint)
            if self._recent
            else 0,
            "total_observations": len(self._all),
        }

    @property
    def observations(self) -> list[Observation]:
        return list(self._all)


def format_observation_message(
    obs: Observation,
    *,
    max_chars: int = 4000,
    untrusted: bool = False,
    source: str = "",
) -> dict[str, Any]:
    """Build the chat message dict for the model from an Observation."""
    payload = obs.to_model_payload(max_chars=max_chars)
    content = json.dumps(payload, default=str, ensure_ascii=False)
    if untrusted:
        from app.runtime.context import wrap_untrusted

        content = wrap_untrusted(content, source=source or f"tool:{obs.tool}", trust="untrusted")
    return {
        "role": "tool",
        "tool_call_id": obs.call_id,
        "name": obs.tool,
        "content": content,
    }


__all__ = [
    "Observation",
    "ObservationTracker",
    "observe_tool_result",
    "format_observation_message",
    "NO_PROGRESS_REPEAT",
]
