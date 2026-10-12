"""Deterministic list/count answers from structured tool results.

When the user asked for a list (emails, drafts, events, repos, …) and a tool
already returned structured rows, the finish summary must contain those rows —
not a meta-description of having listed them. This module restores the
operation-preserving path that existed on the old executor.
"""
from __future__ import annotations

import re
from typing import Any

# Tools whose structured payloads carry list rows the user may have asked for.
_LIST_TOOLS: dict[str, tuple[str, ...]] = {
    "gmail_search_messages": ("messages",),
    "gmail_list_drafts": ("drafts",),
    "google_calendar_list_events": ("events",),
    "github_list_repositories": ("repositories",),
    "github_list_issues": ("issues",),
    "slack_list_channels": ("channels",),
}

_COUNT_TOOLS = frozenset({
    "gmail_count_drafts",
    "github_repository_count",
})

# Goal looks like a request to enumerate / list / show items to the user.
_LIST_GOAL = re.compile(
    r"\b(?:"
    r"list|lists|listing|"
    r"show\s+(?:me\s+)?(?:the\s+)?|"
    r"display|enumerate|bullet|"
    r"latest|recent|newest|"
    r"what(?:'s|\s+are|\s+is)\s+(?:in\s+)?(?:my\s+)?"
    r")\b",
    re.IGNORECASE,
)

# Meta-summary patterns that describe listing without containing the items.
_META_SUMMARY = re.compile(
    r"^\s*(?:"
    r"listed|retrieved|fetched|found|returned|gathered|"
    r"here\s+are\s+the\s+(?:results|emails|messages)|"
    r"i\s+(?:have\s+)?(?:listed|retrieved|found)"
    r")\b",
    re.IGNORECASE,
)

_BULLET_LINE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+\S", re.MULTILINE)


def _rows_from_result(tool_name: str, result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    keys = _LIST_TOOLS.get(str(tool_name) or "", ())
    for key in keys:
        found = result.get(key)
        if isinstance(found, list):
            return [row for row in found if isinstance(row, dict)]
    # Envelope-style payloads (resource/operation/data).
    data = result.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("messages", "drafts", "events", "repositories", "issues", "channels", "items", "results"):
            found = data.get(key)
            if isinstance(found, list):
                return [row for row in found if isinstance(row, dict)]
    return []


def _row_label(row: dict[str, Any]) -> str:
    subject = str(
        row.get("subject")
        or row.get("summary")
        or row.get("title")
        or row.get("name")
        or row.get("login")
        or row.get("number")
        or row.get("id")
        or ""
    ).strip()
    sender = str(
        row.get("from")
        or row.get("sender")
        or row.get("organizer")
        or row.get("full_name")
        or ""
    ).strip()
    when = str(
        row.get("date")
        or row.get("updated_at")
        or row.get("start")
        or ""
    ).strip()
    line = subject or "(untitled)"
    if sender:
        line += f" — {sender}"
    if when:
        line += f" ({when})"
    return line


def _format_list_block(
    tool_name: str,
    result: dict[str, Any],
    rows: list[dict[str, Any]],
) -> str:
    resource = str(result.get("resource") or tool_name or "items").replace("_", " ")
    returned = result.get("returned_count")
    if not isinstance(returned, int):
        returned = len(rows)
    requested = result.get("requested_count")
    header = f"Found {returned} {resource}:"
    if isinstance(requested, int) and requested > returned:
        header = (
            f"Found {returned} {resource} "
            f"(you asked for {requested}; the source only returned {returned}):"
        )
    elif isinstance(requested, int):
        header = f"Found {returned} {resource} (requested {requested}):"
    lines = [header]
    for index, row in enumerate(rows, start=1):
        lines.append(f"{index}. {_row_label(row)}")
    if result.get("has_more") or result.get("next_page_token"):
        lines.append("More results are available beyond this page.")
    # Prefer the tool's own human text when it already enumerates every row.
    human = str(result.get("output") or result.get("human") or "").strip()
    if human and _BULLET_LINE.search(human) and human.count("\n") >= max(1, len(rows) - 1):
        return human
    return "\n".join(lines)


def _format_count_block(tool_name: str, result: dict[str, Any]) -> str | None:
    if not isinstance(result, dict):
        return None
    count = result.get("count")
    if not isinstance(count, int):
        data = result.get("data")
        if isinstance(data, dict) and isinstance(data.get("count"), int):
            count = data["count"]
    if not isinstance(count, int):
        return None
    resource = str(result.get("resource") or tool_name or "items").replace("_", " ")
    exact = result.get("exact", True)
    qualifier = "exactly" if exact else "at least"
    return f"You have {qualifier} {count} {resource}."


def goal_requests_list_or_count(goal: str) -> bool:
    g = (goal or "").strip()
    if not g:
        return False
    if _LIST_GOAL.search(g):
        return True
    if re.search(r"\b(?:how\s+many|count|number\s+of)\b", g, re.IGNORECASE):
        return True
    return False


def summary_looks_like_meta_only(summary: str, min_rows: int = 2) -> bool:
    """True when the summary describes listing but does not enumerate items."""
    text = (summary or "").strip()
    if not text:
        return True
    if _BULLET_LINE.search(text):
        # At least min_rows bullet-like lines → treat as substantive.
        matches = _BULLET_LINE.findall(text)
        if len(matches) >= min_rows:
            return False
    if len(text) > 800 and "\n" in text:
        return False
    return bool(_META_SUMMARY.search(text)) or len(text) < 280


def format_list_answer_from_key_results(
    goal: str,
    key_results: list[dict[str, Any]] | None,
    *,
    existing_summary: str = "",
) -> str | None:
    """Return a deterministic list/count answer when tool evidence supports it.

    Returns None when the goal is not a list/count request, no structured rows
    were found, or the existing summary already enumerates the items.
    """
    if not goal_requests_list_or_count(goal):
        return None
    if not key_results:
        return None

    # Prefer the most recent matching list/count tool result.
    for entry in reversed(key_results):
        if not isinstance(entry, dict):
            continue
        tool_name = str(entry.get("tool") or entry.get("name") or "")
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        if result.get("error") or result.get("error_code"):
            continue

        if tool_name in _COUNT_TOOLS or (
            str(result.get("operation") or "") == "count"
            and isinstance(result.get("count"), int)
        ):
            block = _format_count_block(tool_name, result)
            if block:
                return block

        if tool_name in _LIST_TOOLS or str(result.get("operation") or "") in {
            "list", "search", "get",
        }:
            rows = _rows_from_result(tool_name, result)
            if not rows and str(result.get("operation") or "") == "count":
                continue
            if rows:
                if existing_summary and not summary_looks_like_meta_only(
                    existing_summary, min_rows=min(2, len(rows))
                ):
                    return None
                return _format_list_block(tool_name, result, rows)

        # Fallback: any result that carries messages/drafts/events arrays.
        for key in ("messages", "drafts", "events", "repositories", "issues", "channels"):
            found = result.get(key)
            if isinstance(found, list) and found and all(isinstance(r, dict) for r in found[:3]):
                rows = [r for r in found if isinstance(r, dict)]
                if existing_summary and not summary_looks_like_meta_only(
                    existing_summary, min_rows=min(2, len(rows))
                ):
                    return None
                return _format_list_block(tool_name or key, result, rows)

    return None


def prefer_list_answer(
    goal: str,
    summary: str,
    key_results: list[dict[str, Any]] | None,
) -> str:
    """Replace a meta-only finish summary with a deterministic list when possible."""
    deterministic = format_list_answer_from_key_results(
        goal, key_results, existing_summary=summary
    )
    if not deterministic:
        return summary
    # If the model already wrote a substantive enumeration, keep it.
    if summary and not summary_looks_like_meta_only(summary):
        return summary
    return deterministic
