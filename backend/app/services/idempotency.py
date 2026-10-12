"""Exactly-once execution for tool calls that are not safe to repeat.

The executor's retry loop already refuses to re-run anything that is not
read-only. That protects one execution from retrying itself. It does nothing
about the failures that actually duplicate writes:

* the tool succeeded on the far side and the response was lost in transit;
* the process died between the call and the commit that would have recorded it;
* the user re-ran the task and the planner emitted the same step again.

In all three the caller retries, the action happens twice, and nothing anywhere
reports an error. A duplicate email to a client, a duplicated payment, a second
VAT return filed: the cost of that is not a retry, it is the user's trust.

The mechanism is a claim row written *before* the call and completed *after* it.
Uniqueness on (organization, key) means two concurrent replays race to insert and
exactly one wins, decided by the database rather than by a check that is stale by
the time it is acted on.

Three outcomes, and the third is the interesting one:

* ``CLAIMED`` -- this caller owns the call and must perform it.
* ``REPLAYED`` -- the call already completed; the recorded result is returned.
  The user sees the original outcome, not a second action.
* ``IN_FLIGHT`` -- a claim exists but was never completed. A process died
  mid-call. Whether the action happened is genuinely unknown, so this is refused
  and surfaced rather than guessed: re-running could duplicate a real action,
  and assuming it failed could silently drop one.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.models import ToolExecutionRecord


class ClaimOutcome(str, Enum):
    CLAIMED = "claimed"
    REPLAYED = "replayed"
    IN_FLIGHT = "in_flight"


@dataclass(frozen=True)
class Claim:
    outcome: ClaimOutcome
    result: dict[str, Any] | None = None
    tool_name: str = ""
    recorded_status: str = ""


def key_for(*, task_id: Any, step_id: str, tool_name: str) -> str:
    """A stable key for one planned step of one task.

    Stable across a replay of the same task, and different for genuinely
    different work. It deliberately does not include a timestamp or a random
    component: a key that changes on replay provides no protection at all.
    """
    return f"{task_id}:{step_id}:{tool_name}"[:200]


def digest_arguments(arguments: dict[str, Any]) -> str:
    """Fingerprint the arguments so a key reused with different inputs is caught.

    Sorted keys and a stable separator, because `{"a": 1, "b": 2}` and
    `{"b": 2, "a": 1}` are the same call and must not look like a conflict.
    """
    encoded = json.dumps(arguments, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def claim(
    session_factory: Any,
    *,
    organization_id: Any,
    idempotency_key: str,
    tool_name: str,
    arguments: dict[str, Any],
    task_id: Any = None,
    step_id: str = "",
) -> Claim:
    """Try to take ownership of a tool call. Never raises on a duplicate."""
    from sqlalchemy.exc import IntegrityError

    digest = digest_arguments(arguments)
    organization = _as_uuid(organization_id)

    async with session_factory() as session:
        record = ToolExecutionRecord(
            organization_id=organization,
            idempotency_key=idempotency_key,
            tool_name=tool_name,
            arguments_digest=digest,
            task_id=_as_uuid(task_id),
            step_id=step_id or None,
            status="in_progress",
        )
        session.add(record)
        try:
            # One transaction. The insert either wins the uniqueness race or
            # fails; there is no window in which both callers believe they own
            # the call.
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            # Not every IntegrityError is a lost race. A foreign key or type
            # violation is a bug, and treating it as "someone else already
            # claimed it" would report a phantom in-flight claim for a call that
            # was never made -- blocking the step forever with a reason that is
            # not true.
            if not _is_duplicate_key(exc):
                raise
            return await _inspect_existing(
                session_factory,
                organization_id=organization,
                idempotency_key=idempotency_key,
                arguments_digest=digest,
            )
        return Claim(ClaimOutcome.CLAIMED, tool_name=tool_name)


async def _inspect_existing(
    session_factory: Any,
    *,
    organization_id: Any,
    idempotency_key: str,
    arguments_digest: str,
) -> Claim:
    from sqlalchemy import select

    async with session_factory() as session:
        existing = await session.scalar(
            select(ToolExecutionRecord).where(
                ToolExecutionRecord.organization_id == organization_id,
                ToolExecutionRecord.idempotency_key == idempotency_key,
            )
        )
        if existing is None:  # pragma: no cover - the row was just committed
            return Claim(ClaimOutcome.IN_FLIGHT, recorded_status="unknown")

        if existing.arguments_digest != arguments_digest:
            # The same step emitted with different arguments is a planner or
            # checkpoint bug. Answering with the first call's result would
            # report a success for work that never happened.
            return Claim(
                ClaimOutcome.IN_FLIGHT,
                tool_name=existing.tool_name,
                recorded_status="arguments_mismatch",
            )

        if existing.status == "completed":
            return Claim(
                ClaimOutcome.REPLAYED,
                result=dict(existing.result or {}),
                tool_name=existing.tool_name,
                recorded_status=existing.status,
            )
        if existing.status == "failed":
            # The call was made and is known to have failed. Repeating it is a
            # decision for the planner, not something the store decides.
            return Claim(
                ClaimOutcome.IN_FLIGHT,
                tool_name=existing.tool_name,
                recorded_status="failed",
            )
        return Claim(
            ClaimOutcome.IN_FLIGHT,
            tool_name=existing.tool_name,
            recorded_status=existing.status,
        )


async def complete(
    session_factory: Any,
    *,
    organization_id: Any,
    idempotency_key: str,
    result: dict[str, Any],
) -> None:
    from datetime import datetime, timezone

    from sqlalchemy import update

    async with session_factory() as session:
        await session.execute(
            update(ToolExecutionRecord)
            .where(
                ToolExecutionRecord.organization_id == organization_id,
                ToolExecutionRecord.idempotency_key == idempotency_key,
            )
            .values(
                status="completed",
                result=result,
                completed_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()


async def fail(
    session_factory: Any,
    *,
    organization_id: Any,
    idempotency_key: str,
    error: str,
) -> None:
    from datetime import datetime, timezone

    from sqlalchemy import update

    async with session_factory() as session:
        await session.execute(
            update(ToolExecutionRecord)
            .where(
                ToolExecutionRecord.organization_id == organization_id,
                ToolExecutionRecord.idempotency_key == idempotency_key,
            )
            .values(
                status="failed",
                error=error[:4000],
                completed_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()


def _is_duplicate_key(exc: Exception) -> bool:
    """True only for the uniqueness violation that means 'already claimed'."""
    text = str(getattr(exc, "orig", exc)).lower()
    return "uq_tool_execution_key" in text or "duplicate key" in text


def _as_uuid(value: Any) -> Any:
    if value is None or isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))