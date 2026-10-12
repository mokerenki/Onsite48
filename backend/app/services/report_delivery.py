"""Deterministic delivery policy for complex Onsite48 task results.

This module deliberately makes the report decision in application code instead of
asking an LLM to judge layout risk.  The original answer remains in a
user-isolated Markdown artifact; the chat receives only a compact delivery note.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.artifact_contract import ArtifactDescriptor, MARKDOWN_EVENT_CONTENT_TYPE, signed_download_url
from app.services.attachment_store import attachment_store

INLINE_MAX_CHARS = max(1_000, int(os.getenv("MARKDOWN_REPORT_INLINE_MAX_CHARS", "8000")))


@dataclass(frozen=True)
class TableMetrics:
    columns: int
    rows: int
    max_cell_chars: int


def _cells(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")] if "|" in stripped else []


def table_metrics(markdown: str) -> list[TableMetrics]:
    """Return metrics for Markdown pipe tables with a valid divider row."""
    lines = markdown.splitlines()
    metrics: list[TableMetrics] = []
    index = 0
    divider_cell = re.compile(r"^:?-{3,}:?$")
    while index + 1 < len(lines):
        header = _cells(lines[index])
        divider = _cells(lines[index + 1])
        if len(header) >= 2 and len(header) == len(divider) and all(divider_cell.match(cell) for cell in divider):
            row_count = 0
            widest = max((len(cell) for cell in header), default=0)
            cursor = index + 2
            while cursor < len(lines):
                row = _cells(lines[cursor])
                if len(row) != len(header):
                    break
                row_count += 1
                widest = max(widest, *(len(cell) for cell in row))
                cursor += 1
            metrics.append(TableMetrics(columns=len(header), rows=row_count, max_cell_chars=widest))
            index = cursor
        else:
            index += 1
    return metrics


def delivery_reason(markdown: str) -> str | None:
    """Return a deterministic report reason, or None when inline chat is safe."""
    if len(markdown) > INLINE_MAX_CHARS:
        return f"the response is longer than {INLINE_MAX_CHARS:,} characters"
    tables = table_metrics(markdown)
    # Tables are always artifacts. Even a small table can force horizontal
    # scrolling in a narrow chat pane, and keeping all tabular results in a
    # Markdown file makes delivery deterministic across desktop and mobile.
    if len(tables) > 1:
        return "the response contains multiple tables"
    if tables:
        return "the response contains a table"
    return None


def report_name(title: str) -> str:
    # Report titles often originate from uploaded/requested filenames. Drop an
    # existing suffix before deriving the Markdown artifact name so
    # ``comparison.json`` becomes ``comparison.md`` rather than
    # ``comparison-json.md``.
    stem = Path(title.strip() or "onsite48-report").stem
    safe = re.sub(r"[^A-Za-z0-9]+", "-", stem.lower()).strip("-")[:72]
    return f"{safe or 'onsite48-report'}.md"


def chat_summary(markdown: str, reason: str, filename: str) -> str:
    """Produce a deterministic, compact delivery note without echoing a table."""
    heading = next((line.lstrip("#").strip() for line in markdown.splitlines() if line.lstrip().startswith("#") and line.lstrip("#").strip()), "Your requested analysis")
    return (
        f"{heading} has been prepared as a downloadable Markdown report because {reason}. "
        f"Open **{filename}** for the complete structured result."
    )


def maybe_create_markdown_report(
    *,
    organization_id: str,
    user_id: str,
    title: str,
    final_answer: str | None,
) -> dict[str, Any] | None:
    answer = (final_answer or "").strip()
    if not answer:
        return None
    reason = delivery_reason(answer)
    if not reason:
        return None
    filename = report_name(title)
    artifact = attachment_store.save_generated_markdown(
        organization_id=organization_id,
        user_id=user_id,
        filename=filename,
        markdown=answer,
        source_title=title,
    )
    descriptor = ArtifactDescriptor(
        attachment_id=str(artifact["id"]),
        name=str(artifact["name"]),
        content_type=MARKDOWN_EVENT_CONTENT_TYPE,
        size=int(artifact["size"]),
        sha256=str(artifact["sha256"]),
        artifact_type=str(artifact["artifact_type"]),
        download_url=signed_download_url(artifact["id"]),
        source_title=str(artifact.get("source_title") or title),
        provenance={
            "kind": "generated_report",
            "source_title": str(artifact.get("source_title") or title),
            "reason": reason,
            "generated_at": str(artifact.get("created_at") or ""),
        },
        preview=answer[:500],
    )
    return {
        **descriptor.event_payload(),
        "artifacts": [descriptor.event_payload()],
        "reason": reason,
        "summary": chat_summary(answer, reason, descriptor.name),
    }


def apply_report_delivery(
    event: dict[str, Any], *, organization_id: str, user_id: str, title: str
) -> dict[str, Any]:
    """Replace an oversized completion answer with a report delivery event."""
    if event.get("type") != "task_complete":
        return event
    artifact = maybe_create_markdown_report(
        organization_id=organization_id,
        user_id=user_id,
        title=title,
        final_answer=event.get("final_answer"),
    )
    if artifact is None:
        return event
    return {**event, "final_answer": artifact["summary"], "artifact": artifact, "artifacts": [artifact]}


def prepare_report_delivery(
    event: dict[str, Any], *, organization_id: str, user_id: str, title: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the durable and safe chat event for a completion.

    For a generated report, the Markdown artifact is the durable full result.
    PostgreSQL stores the compact delivery note and artifact metadata so a page
    refresh never reintroduces an oversized table into the chat transcript.
    """
    try:
        client_event = apply_report_delivery(
            event, organization_id=organization_id, user_id=user_id, title=title
        )
    except Exception:
        # Report delivery is a presentation enhancement; a storage failure must
        # never discard a completed agent answer.
        return event, event
    artifact = client_event.get("artifact")
    if artifact:
        return client_event, client_event
    return event, event