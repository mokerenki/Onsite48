"""Deterministic recurrence and delivery-window calculations.

All public functions accept UTC-aware datetimes and return UTC-aware datetimes.
Local wall-clock interpretation is performed with IANA zoneinfo identifiers.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
WEEKDAYS = set(range(1, 8))  # ISO Monday=1 through Sunday=7


def zone_for(value: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(value or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _local_datetime(policy: dict[str, Any], local_date: date) -> datetime:
    raw_time = str(policy.get("local_time") or "08:00")
    hour, minute = (int(part) for part in raw_time.split(":", 1))
    return datetime.combine(local_date, time(hour=hour, minute=minute), tzinfo=zone_for(str(policy.get("timezone") or "UTC")))


def _roundtrip_valid(value: datetime) -> bool:
    zone = value.tzinfo
    if zone is None:
        return False
    return value.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == value.replace(tzinfo=None)


def next_occurrence(policy: dict[str, Any], after_utc: datetime) -> datetime:
    """Return the next valid occurrence after the supplied instant.

    Skipped DST wall times are advanced to the next local minute. Month days
    that do not exist in a month are skipped rather than silently moved.
    """
    if after_utc.tzinfo is None:
        raise ValueError("after_utc must be timezone-aware")
    after = after_utc.astimezone(UTC)
    frequency = str(policy.get("frequency") or policy.get("schedule_type") or "once").lower()
    if frequency == "once":
        run_at = policy.get("run_at")
        if isinstance(run_at, str):
            run_at = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
        if isinstance(run_at, datetime):
            candidate = run_at if run_at.tzinfo else run_at.replace(tzinfo=UTC)
            return candidate.astimezone(UTC)
        raise ValueError("once schedules require run_at")

    local_after = after.astimezone(zone_for(str(policy.get("timezone") or "UTC")))
    candidate_date = local_after.date()
    for offset in range(0, 367):
        current = candidate_date + timedelta(days=offset)
        allowed = True
        if frequency == "weekly":
            weekdays = {int(item) for item in policy.get("weekdays", []) if int(item) in WEEKDAYS}
            allowed = current.isoweekday() in (weekdays or {1})
        elif frequency == "monthly":
            days = {int(item) for item in policy.get("month_days", []) if 1 <= int(item) <= 31}
            allowed = current.day in (days or {1})
        elif frequency not in {"daily", "interval"}:
            raise ValueError(f"Unsupported schedule frequency: {frequency}")
        if not allowed:
            continue
        local_candidate = _local_datetime(policy, current)
        if not _roundtrip_valid(local_candidate):
            local_candidate += timedelta(minutes=30)
        candidate = local_candidate.astimezone(UTC)
        if candidate > after:
            return candidate
    raise ValueError("Could not calculate a future occurrence within one year")


def is_quiet(policy: dict[str, Any], instant_utc: datetime) -> bool:
    quiet = policy.get("quiet_hours") or {}
    if not quiet.get("enabled"):
        return False
    zone = zone_for(str(policy.get("timezone") or "UTC"))
    local = instant_utc.astimezone(zone).time()
    start = time.fromisoformat(str(quiet.get("start") or "22:00"))
    end = time.fromisoformat(str(quiet.get("end") or "07:00"))
    if start <= end:
        return start <= local < end
    return local >= start or local < end


def is_business_window(policy: dict[str, Any], instant_utc: datetime) -> bool:
    business = policy.get("business_hours") or {}
    if not business.get("enabled"):
        return True
    zone = zone_for(str(policy.get("timezone") or "UTC"))
    local = instant_utc.astimezone(zone)
    weekdays = {int(item) for item in business.get("weekdays", [1, 2, 3, 4, 5])}
    if local.isoweekday() not in weekdays:
        return False
    start = time.fromisoformat(str(business.get("start") or "09:00"))
    end = time.fromisoformat(str(business.get("end") or "17:00"))
    return start <= local.time() < end


def apply_calendar_policy(policy: dict[str, Any], candidate_utc: datetime, busy_intervals: list[tuple[datetime, datetime]] | None = None) -> dict[str, Any]:
    """Apply a caller-provided calendar result without making connector calls."""
    calendar = policy.get("calendar_policy") or {}
    if not calendar.get("enabled") or not busy_intervals:
        return {"decision": "run", "scheduled_for": candidate_utc}
    overlaps = any(start <= candidate_utc < end for start, end in busy_intervals)
    if not overlaps:
        return {"decision": "run", "scheduled_for": candidate_utc}
    behavior = str(calendar.get("busy_behavior") or "delay")
    if behavior == "skip":
        return {"decision": "skip", "scheduled_for": candidate_utc, "reason": "calendar_busy"}
    if behavior == "notify":
        return {"decision": "run", "scheduled_for": candidate_utc, "reason": "calendar_busy"}
    next_free = max(end for start, end in busy_intervals if start <= candidate_utc < end)
    return {"decision": "delay", "scheduled_for": next_free.astimezone(UTC), "reason": "calendar_busy"}