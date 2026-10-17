"""Phase 3 — Context engineering for the agent runtime.

Builds a budgeted message list for the model from run state:

* **Pinned prefix**: system + goal (never dropped)
* **Plan / todo**: hierarchical plan or todo.md (high priority)
* **Recent window**: last N messages kept verbatim
* **Older tail**: extractively summarized into a single memory block
* **Errors & approvals**: never compacted away

Token accounting is character-based (≈4 chars/token) so unit tests stay
deterministic without a tokenizer dependency.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

# Defaults — overridable via env for production tuning.
DEFAULT_BUDGET_TOKENS = int(os.environ.get("CONTEXT_BUDGET_TOKENS", "20000"))
DEFAULT_RECENT_MESSAGES = int(os.environ.get("CONTEXT_RECENT_MESSAGES", "12"))
CHARS_PER_TOKEN = 4


def estimate_tokens(messages: list[dict[str, Any]] | str | None) -> int:
    """Rough token estimate: 1 token ≈ 4 characters."""
    if messages is None:
        return 0
    if isinstance(messages, str):
        return max(1, len(messages) // CHARS_PER_TOKEN) if messages else 0
    total_chars = 0
    for msg in messages:
        if not isinstance(msg, dict):
            total_chars += len(str(msg))
            continue
        for key in ("content", "summary", "question", "name"):
            v = msg.get(key)
            if isinstance(v, str):
                total_chars += len(v)
        for key in ("tool_calls", "options"):
            v = msg.get(key)
            if isinstance(v, str):
                total_chars += len(v)
            elif v is not None:
                try:
                    total_chars += len(json.dumps(v, default=str))
                except (TypeError, ValueError):
                    total_chars += len(str(v))
    return total_chars // CHARS_PER_TOKEN


def _is_protected(msg: dict[str, Any]) -> bool:
    """Messages that must survive compaction."""
    content = str(msg.get("content") or "")
    role = str(msg.get("role") or "")
    if role == "system":
        return True
    if "error_code" in content or '"outcome": "error"' in content or '"outcome": "timeout"' in content:
        return True
    if "needs_input" in content or "approval" in content.lower():
        return True
    if content.startswith("# Plan:") or content.startswith("Current todo.md"):
        return True
    return False


def extractive_summary(messages: list[dict[str, Any]], *, max_chars: int = 2000) -> str:
    """Build a deterministic extractive summary of older turns.

    Prefers tool outcomes and short assistant decisions; drops bulky dumps.
    """
    lines: list[str] = []
    for msg in messages:
        role = str(msg.get("role") or "unknown")
        content = str(msg.get("content") or "")
        name = str(msg.get("name") or "")
        if role == "tool":
            # Prefer structured observation JSON
            summary = content
            try:
                body = json.loads(content)
                if isinstance(body, dict):
                    parts = []
                    if body.get("tool") or name:
                        parts.append(str(body.get("tool") or name))
                    if body.get("outcome"):
                        parts.append(f"outcome={body['outcome']}")
                    if body.get("summary"):
                        parts.append(str(body["summary"])[:200])
                    if body.get("error_code"):
                        parts.append(f"error={body['error_code']}")
                    summary = " | ".join(parts) if parts else content[:200]
            except (json.JSONDecodeError, TypeError):
                summary = content[:200]
            lines.append(f"- tool:{name or '?'}: {summary}")
        elif role == "assistant":
            if content.strip():
                lines.append(f"- assistant: {content.strip()[:180]}")
            elif msg.get("tool_calls"):
                try:
                    calls = msg["tool_calls"]
                    names = []
                    for c in calls if isinstance(calls, list) else []:
                        fn = (c.get("function") or {}).get("name") if isinstance(c, dict) else None
                        if fn:
                            names.append(fn)
                    if names:
                        lines.append(f"- assistant called: {', '.join(names[:6])}")
                except Exception:
                    pass
        elif role == "user":
            if content.startswith("Goal:"):
                continue  # already pinned
            if content.strip():
                lines.append(f"- user: {content.strip()[:180]}")

    text = "Prior context (summarized):\n" + "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n…[truncated]"
    return text


@dataclass
class ContextPack:
    """Result of context engineering for one model turn."""

    messages: list[dict[str, Any]]
    tokens_estimated: int
    budget_tokens: int
    compacted: bool = False
    summarized_count: int = 0
    dropped_count: int = 0
    sections: dict[str, int] = field(default_factory=dict)

    def to_diagnostics(self) -> dict[str, Any]:
        return {
            "tokens_estimated": self.tokens_estimated,
            "budget_tokens": self.budget_tokens,
            "compacted": self.compacted,
            "summarized_count": self.summarized_count,
            "dropped_count": self.dropped_count,
            "sections": dict(self.sections),
            "message_count": len(self.messages),
        }


@dataclass
class ContextEngine:
    """Budgeted context packer for the agent loop."""

    budget_tokens: int = DEFAULT_BUDGET_TOKENS
    recent_messages: int = DEFAULT_RECENT_MESSAGES

    def pack(
        self,
        *,
        system: str,
        goal: str,
        history: list[dict[str, Any]],
        plan_block: str = "",
        extra_pinned: list[dict[str, Any]] | None = None,
    ) -> ContextPack:
        """Assemble a budgeted message list.

        Order:
          1. system
          2. goal
          3. plan (optional)
          4. extra pinned (optional)
          5. summary of older history (if needed)
          6. recent history (verbatim)
        """
        pinned: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Goal:\n{goal}"},
        ]
        sections = {"system": 1, "goal": 1, "plan": 0, "summary": 0, "recent": 0, "extra": 0}

        if plan_block and plan_block.strip():
            pinned.append({"role": "user", "content": plan_block.strip()})
            sections["plan"] = 1

        for msg in extra_pinned or []:
            if isinstance(msg, dict):
                pinned.append(msg)
                sections["extra"] += 1

        history = [m for m in (history or []) if isinstance(m, dict)]
        recent_n = max(2, int(self.recent_messages))

        if len(history) <= recent_n:
            recent = list(history)
            older: list[dict[str, Any]] = []
        else:
            recent = list(history[-recent_n:])
            older = list(history[:-recent_n])

        # Always hoist protected messages from older into recent if missing
        protected_older = [m for m in older if _is_protected(m)]
        if protected_older:
            # Keep protected items just before the recent window
            recent = protected_older + recent
            older = [m for m in older if not _is_protected(m)]

        messages = list(pinned)
        summarized_count = 0
        dropped_count = 0
        compacted = False

        if older:
            summary_text = extractive_summary(older, max_chars=min(4000, self.budget_tokens * CHARS_PER_TOKEN // 4))
            messages.append({"role": "user", "content": summary_text})
            sections["summary"] = 1
            summarized_count = len(older)
            compacted = True

        messages.extend(recent)
        sections["recent"] = len(recent)

        # If still over budget, shrink recent from the front (oldest of recent)
        tokens = estimate_tokens(messages)
        if tokens > self.budget_tokens and len(recent) > 2:
            compacted = True
            # Drop unprotected recent messages from the front until under budget
            keep_tail = list(recent)
            while keep_tail and estimate_tokens(pinned + (messages[len(pinned) : len(pinned) + sections["summary"]] if sections["summary"] else []) + keep_tail) > self.budget_tokens:
                # Prefer dropping non-protected from the front
                drop_idx = next((i for i, m in enumerate(keep_tail) if not _is_protected(m)), None)
                if drop_idx is None:
                    break
                keep_tail.pop(drop_idx)
                dropped_count += 1

            # Rebuild
            messages = list(pinned)
            if sections["summary"]:
                messages.append({"role": "user", "content": extractive_summary(older, max_chars=min(2000, self.budget_tokens * CHARS_PER_TOKEN // 6))})
            messages.extend(keep_tail)
            sections["recent"] = len(keep_tail)
            tokens = estimate_tokens(messages)

        # Last resort: hard truncate largest contents
        if tokens > self.budget_tokens:
            compacted = True
            messages = self._hard_trim(messages, self.budget_tokens)
            tokens = estimate_tokens(messages)

        return ContextPack(
            messages=messages,
            tokens_estimated=tokens,
            budget_tokens=self.budget_tokens,
            compacted=compacted,
            summarized_count=summarized_count,
            dropped_count=dropped_count,
            sections=sections,
        )

    def _hard_trim(self, messages: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
        """Truncate individual message contents to fit budget."""
        out: list[dict[str, Any]] = []
        used = 0
        for i, msg in enumerate(messages):
            m = dict(msg)
            content = str(m.get("content") or "")
            # Always keep system + goal short path
            if i < 2:
                out.append(m)
                used += estimate_tokens([m])
                continue
            remaining = max(200, (budget - used) * CHARS_PER_TOKEN)
            if len(content) > remaining:
                m["content"] = content[: remaining - 20] + "\n…[trimmed]"
            out.append(m)
            used += estimate_tokens([m])
            if used >= budget:
                break
        return out


__all__ = [
    "ContextEngine",
    "ContextPack",
    "estimate_tokens",
    "extractive_summary",
    "DEFAULT_BUDGET_TOKENS",
    "DEFAULT_RECENT_MESSAGES",
]
