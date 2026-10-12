"""Cross-task cost aggregation for operators.

Per-task cost lives in ``TaskThread.cost_metrics``, which is enough to render a
number next to a task and useless for the questions an operator actually asks:
what did we spend this month, which user is expensive, is burn rate climbing.

This module answers those from the stored metrics without inventing prices, and
it is explicit about the difference between a measured total and a lower bound.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cost_accounting import unpriced_token_fraction
from app.models import TaskThread, utcnow


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def empty_summary() -> dict[str, Any]:
    return {
        "task_count": 0,
        "total_cost_usd": 0.0,
        # True when at least one task had unpriced calls, meaning the total is a
        # floor and not a measurement. Never quietly reported as exact.
        "total_cost_usd_is_lower_bound": False,
        "tasks_with_unpriced_calls": 0,
        "known_model_calls": 0,
        "unknown_cost_model_calls": 0,
        "unpriced_input_tokens": 0,
        "unpriced_output_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "by_provider": {},
        "by_role": {},
    }


def summarize_cost_metrics(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate raw ``cost_metrics`` mappings into one operator summary.

    Pure, so the aggregation is testable without a database and identical
    whether it is fed by the query below or by a fixture.
    """
    summary = empty_summary()
    tasks_with_unpriced = 0

    for metrics in rows:
        if not isinstance(metrics, Mapping):
            continue
        summary["task_count"] += 1
        summary["total_cost_usd"] += _as_float(metrics.get("total_cost_usd"))
        summary["known_model_calls"] += _as_int(metrics.get("known_model_calls"))
        summary["unknown_cost_model_calls"] += _as_int(metrics.get("unknown_cost_model_calls"))
        summary["unpriced_input_tokens"] += _as_int(metrics.get("unpriced_input_tokens"))
        summary["unpriced_output_tokens"] += _as_int(metrics.get("unpriced_output_tokens"))

        if metrics.get("total_cost_usd_is_lower_bound"):
            summary["total_cost_usd_is_lower_bound"] = True
            tasks_with_unpriced += 1

        for key, record in (metrics.get("by_provider") or {}).items():
            if not isinstance(record, Mapping):
                continue
            bucket = summary["by_provider"].setdefault(
                str(key),
                {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                    "unknown_cost_calls": 0,
                },
            )
            bucket["input_tokens"] += _as_int(record.get("input_tokens"))
            bucket["output_tokens"] += _as_int(record.get("output_tokens"))
            bucket["cost_usd"] += _as_float(record.get("cost_usd"))
            bucket["unknown_cost_calls"] += _as_int(record.get("unknown_cost_calls"))

        for key, record in (metrics.get("by_role") or {}).items():
            if not isinstance(record, Mapping):
                continue
            bucket = summary["by_role"].setdefault(
                str(key), {"calls": 0, "cost_usd": 0.0, "unknown_cost_calls": 0}
            )
            bucket["calls"] += _as_int(record.get("calls"))
            bucket["cost_usd"] += _as_float(record.get("cost_usd"))
            bucket["unknown_cost_calls"] += _as_int(record.get("unknown_cost_calls"))

    summary["tasks_with_unpriced_calls"] = tasks_with_unpriced
    summary["unpriced_token_fraction"] = unpriced_token_fraction(
        {
            "unpriced_input_tokens": summary["unpriced_input_tokens"],
            "unpriced_output_tokens": summary["unpriced_output_tokens"],
            "by_provider": {
                key: {
                    "input_tokens": value["input_tokens"],
                    "output_tokens": value["output_tokens"],
                }
                for key, value in summary["by_provider"].items()
            },
        }
    )
    return summary


async def load_cost_summary(
    session: AsyncSession,
    *,
    organization_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 5000,
) -> dict[str, Any]:
    """Summarize spend for an organization over a window.

    Bounded by ``limit`` because ``cost_metrics`` is a JSON column and cannot be
    aggregated in the database; an unbounded scan over a growing task history is
    a real outage waiting to happen. The limit is reported in the result so a
    truncated answer is never mistaken for a complete one.
    """
    statement = select(TaskThread.cost_metrics, TaskThread.created_at).order_by(
        TaskThread.created_at.desc()
    )
    if organization_id is not None:
        statement = statement.where(TaskThread.organization_id == organization_id)
    if since is not None:
        statement = statement.where(TaskThread.created_at >= since)
    if until is not None:
        statement = statement.where(TaskThread.created_at < until)

    bounded = max(1, min(int(limit), 20000))
    result = await session.execute(statement.limit(bounded + 1))
    rows = list(result.all())
    truncated = len(rows) > bounded
    rows = rows[:bounded]

    summary = summarize_cost_metrics(row[0] for row in rows)
    summary["truncated"] = truncated
    summary["row_limit"] = bounded
    summary["window_start"] = since.isoformat() if since else None
    summary["window_end"] = until.isoformat() if until else None
    summary["generated_at"] = utcnow().isoformat()
    return summary


__all__ = [
    "empty_summary",
    "load_cost_summary",
    "summarize_cost_metrics",
]