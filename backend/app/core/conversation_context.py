"""Durable multi-turn conversation context for the planner and executor.

The WebSocket realtime layer persists the conversation transcript on the
TaskThread, but the graph nodes historically only saw the *latest* user
message. That made follow-ups like "where are the other 5?" produce
"no context provided" answers. This module builds a compact, structured
view of the conversation that is safe to inject into planner and
synthesis prompts without leaking tool payloads verbatim.

Design constraints:
- Deterministic. No model calls. No network.
- Bounded. Never grows the prompt beyond a hard char budget. Each builder has
  its own budget, because the transcript and the entity ledger are different
  purposes with different value densities.
- Explicit. Recent turns are shown verbatim; older turns are summarized
  to their role + first 240 characters.
- Entity-aware. If a prior step produced a list of entities (emails,
  events, issues), we surface the count and the labels so follow-up
  references ("the other 5") can resolve.
"""

from __future__ import annotations

from typing import Any


_MAX_TOTAL_CHARS = 6000
_RECENT_TURNS_VERBATIM = 6
_RECENT_TURN_MAX_CHARS = 900
_OLD_TURN_MAX_CHARS = 240
_MAX_ENTITY_LABELS = 25

# The entity ledger is a different purpose from the transcript, so it gets its
# own budget rather than sharing the conversation's. Before this existed the
# ledger was unbounded: 100 steps x 25 labels produced 178,646 characters
# (~45k tokens) injected into a single planner or executor prompt, paid on every
# call, and free to drown the actual ask. Step count, not per-step size, was the
# driver.
_ENTITY_MAX_CHARS = 3000
_ENTITY_MAX_STEPS = 12
_ENTITY_MAX_LABELS_TOTAL = 60
_ENTITY_ELISION_MARKER = "…[older items elided from context]"


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _turn_role(turn: dict[str, Any]) -> str:
    role = str(turn.get("role") or "user").lower()
    return "Assistant" if role == "assistant" else "User"


def build_conversation_context(
    task_metadata: dict[str, Any] | None,
    *,
    current_user_message: str | None = None,
    max_total_chars: int = _MAX_TOTAL_CHARS,
) -> str:
    """Return a bounded, ordered transcript suitable for prompt injection.

    The latest user message is appended last so that the model sees the
    immediate ask after its own history.
    """
    metadata = task_metadata or {}
    conversation = metadata.get("conversation") or []
    if not isinstance(conversation, list):
        conversation = []

    normalized: list[tuple[str, str]] = []
    for turn in conversation:
        if not isinstance(turn, dict):
            continue
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        normalized.append((_turn_role(turn), content))

    if current_user_message:
        latest = " ".join(current_user_message.split())
        if not normalized or normalized[-1] != ("User", latest):
            normalized.append(("User", latest))

    if not normalized:
        return ""

    recent = normalized[-_RECENT_TURNS_VERBATIM:]
    older = normalized[: -_RECENT_TURNS_VERBATIM] if len(normalized) > _RECENT_TURNS_VERBATIM else []

    lines: list[str] = []
    if older:
        lines.append("Earlier in this conversation:")
        for role, content in older[-12:]:
            lines.append(f"- {role}: {_clip(content, _OLD_TURN_MAX_CHARS)}")
        lines.append("")
    lines.append("Most recent turns:")
    for role, content in recent:
        lines.append(f"{role}: {_clip(content, _RECENT_TURN_MAX_CHARS)}")

    block = "\n".join(lines)
    if len(block) > max_total_chars:
        block = block[-max_total_chars:]
        # Keep the header so the model knows this is a bounded view.
        block = "…[older history truncated]\n" + block
    return block


def _entity_lines(result: dict[str, Any]) -> list[str]:
    """Render one tool result as ledger lines, or nothing if it carries no entities."""
    step = str(result.get("step") or "").strip()
    tool = str(result.get("tool") or "").strip()
    if not tool:
        return []

    output = result.get("output")
    labels: list[str] = []
    count: int | None = None
    if isinstance(output, dict):
        for key in ("messages", "drafts", "events", "issues", "repositories", "results", "items"):
            value = output.get(key)
            if isinstance(value, list):
                count = len(value)
                for row in value[:_MAX_ENTITY_LABELS]:
                    if not isinstance(row, dict):
                        continue
                    label = (
                        row.get("subject")
                        or row.get("title")
                        or row.get("name")
                        or row.get("summary")
                        or row.get("full_name")
                        or row.get("key")
                        or row.get("id")
                    )
                    if label:
                        labels.append(str(label)[:160])
                break
    elif isinstance(output, str):
        # Nothing structured; skip.
        return []

    if count is None and not labels:
        return []

    header = f"- {tool}"
    if step:
        header += f" ({step})"
    if count is not None:
        header += f": returned {count} item(s)"
    return [header, *(f"    • {label}" for label in labels)]


def build_entity_context(
    results: list[dict[str, Any]] | None,
    *,
    max_chars: int = _ENTITY_MAX_CHARS,
    max_steps: int = _ENTITY_MAX_STEPS,
) -> str:
    """Summarize prior tool results as a compact, bounded entity ledger.

    This is what allows a follow-up like "where are the other 5?" to resolve
    against a previously returned list rather than looking like a fresh request.
    We surface counts and stable identifiers only — never the full tool payload.

    Budgeted by keeping the most recent results. A follow-up refers to what was
    just returned, so recency is the signal that matters; older results are
    dropped and the omission is stated rather than hidden, so the model can tell
    the difference between "there were no more items" and "we stopped showing
    you items".
    """
    if not results:
        return ""

    per_step = [lines for lines in (_entity_lines(r) for r in results if isinstance(r, dict)) if lines]
    if not per_step:
        return ""

    elided_steps = max(0, len(per_step) - max_steps)
    kept = per_step[-max_steps:] if max_steps > 0 else []

    # Assemble from the newest backwards so the budget, when exceeded, costs the
    # oldest results rather than the most relevant ones. Reserve room for the
    # marker up front (we cannot know whether it is needed until the end) and
    # emit it exactly once.
    budget = max_chars - len(_ENTITY_ELISION_MARKER) - 1

    chosen: list[str] = []
    labels_used = 0
    dropped_for_budget = False
    for lines in reversed(kept):
        added = sum(len(line) + 1 for line in lines)
        step_labels = len(lines) - 1
        if labels_used + step_labels > _ENTITY_MAX_LABELS_TOTAL:
            dropped_for_budget = True
            continue
        if added > budget:
            dropped_for_budget = True
            continue
        chosen.insert(0, "\n".join(lines))
        budget -= added
        labels_used += step_labels

    if not chosen:
        return f"{_ENTITY_ELISION_MARKER}\nNo items retained within the context budget."

    marker = (
        f"{_ENTITY_ELISION_MARKER}\n"
        if (elided_steps or dropped_for_budget) else ""
    )
    return marker + "Previously returned items (reference by label or index):\n" + "\n".join(chosen)


__all__ = ["build_conversation_context", "build_entity_context"]