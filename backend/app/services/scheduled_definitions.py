"""Scheduled Task Definition Schema — YAML-based schedule definitions.

Allows operators to define recurring agent tasks as YAML files that are
loaded at startup and reconciled with the database. Each definition produces
a ScheduledAgentTask row (if not already present) and drives the proactive
worker.

YAML schema (schedule.yaml):
```yaml
schedules:
  - title: "Weekly Market Update"
    prompt: "Research the top 3 market movers this week and summarize."
    schedule_type: weekly
    timezone: America/New_York
    local_time: "09:00"
    weekdays: [1]  # Monday
    enabled: true
    autonomy_policy:
      mode: autonomous
      additional_allowed_tools: ["search_web", "read_pdf"]
    quiet_hours:
      start: "20:00"
      end: "08:00"
    tags: ["market-research", "weekly"]

  - title: "Daily Codebase Health"
    prompt: "Check the build status, test coverage, and open PRs."
    schedule_type: daily
    timezone: UTC
    local_time: "06:00"
    enabled: true
    autonomy_policy:
      mode: approval_required
```
"""
from __future__ import annotations

import os
import structlog
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ScheduledAgentTask
from app.services.recurrence import next_occurrence

logger = structlog.get_logger("onsite48.scheduled_def")


@dataclass
class ScheduledTaskDefinition:
    """A single YAML-defined scheduled task."""
    title: str
    prompt: str
    schedule_type: str = "once"
    timezone: str = "UTC"
    local_date: Optional[str] = None
    local_time: Optional[str] = None
    run_at: Optional[str] = None
    weekdays: list[int] = field(default_factory=list)
    month_days: list[int] = field(default_factory=list)
    quiet_hours: dict[str, Any] = field(default_factory=dict)
    business_hours: dict[str, Any] = field(default_factory=dict)
    calendar_policy: dict[str, Any] = field(default_factory=dict)
    autonomy_policy: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    tags: list[str] = field(default_factory=list)
    user_id: str = ""
    organization_id: str = ""
    _definition_key: str = ""  # internal: for dedup tracking


class ScheduledDefinitionLoader:
    """Loads YAML schedule definitions and reconciles them with the database."""

    def __init__(self, definitions_dir: str | None = None):
        self.definitions_dir = Path(
            definitions_dir or os.environ.get("SCHEDULE_DEFINITIONS_DIR", "/etc/onsite48/schedules")
        )
        self._loaded: dict[str, list[ScheduledTaskDefinition]] = {}

    def load_definitions(self) -> dict[str, list[ScheduledTaskDefinition]]:
        """Load all YAML schedule definition files from the directory."""
        if not self.definitions_dir.exists():
            logger.info("schedule_loader.no_definitions_dir", path=str(self.definitions_dir))
            return {}

        loaded: dict[str, list[ScheduledTaskDefinition]] = {}
        for yaml_file in sorted(self.definitions_dir.glob("*.yaml")):
            try:
                import yaml
                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
                if not data or not isinstance(data, dict):
                    continue
                org_id = data.get("org_id", data.get("organization_id", "default"))
                user_id = data.get("user_id", "")
                schedules = data.get("schedules", [])
                definitions: list[ScheduledTaskDefinition] = []
                for sched in schedules:
                    if not isinstance(sched, dict):
                        continue
                    defn = ScheduledTaskDefinition(
                        title=sched.get("title", "")[:240],
                        prompt=sched.get("prompt", "")[:12000],
                        schedule_type=sched.get("schedule_type", "once"),
                        timezone=sched.get("timezone", "UTC"),
                        local_date=sched.get("local_date"),
                        local_time=sched.get("local_time"),
                        run_at=sched.get("run_at"),
                        weekdays=sched.get("weekdays", []),
                        month_days=sched.get("month_days", []),
                        quiet_hours=sched.get("quiet_hours", {}),
                        business_hours=sched.get("business_hours", {}),
                        calendar_policy=sched.get("calendar_policy", {}),
                        autonomy_policy=sched.get("autonomy_policy", {"mode": "approval_required"}),
                        enabled=sched.get("enabled", True),
                        tags=sched.get("tags", []),
                        user_id=sched.get("user_id", user_id),
                        organization_id=str(org_id),
                    )
                    defn._definition_key = f"{org_id}:{user_id}:{defn.title}:{yaml_file.name}"
                    definitions.append(defn)
                loaded[str(org_id)] = definitions
                self._loaded[str(org_id)] = definitions
                logger.info("schedule_loader.loaded", file=yaml_file.name, org_id=org_id, count=len(definitions))
            except Exception as exc:
                logger.error("schedule_loader.load_error", file=yaml_file.name, error=str(exc))

        return loaded

    @staticmethod
    def _build_schedule_policy(defn: ScheduledTaskDefinition, run_at: datetime) -> dict[str, Any]:
        return {
            "version": 1,
            "frequency": defn.schedule_type,
            "timezone": defn.timezone,
            "local_time": defn.local_time or run_at.astimezone(
                __import__("zoneinfo").ZoneInfo(defn.timezone)
            ).strftime("%H:%M"),
            "weekdays": sorted(set(defn.weekdays)),
            "month_days": sorted(set(defn.month_days)),
            "run_at": run_at.isoformat(),
            "quiet_hours": defn.quiet_hours,
            "business_hours": defn.business_hours,
            "calendar_policy": defn.calendar_policy,
        }

    async def reconcile(
        self,
        session: AsyncSession,
        *,
        user_override: Optional[Any] = None,
        org_override: Optional[Any] = None,
    ) -> dict[str, Any]:
        """Reconcile loaded YAML definitions with the database.

        - Definitions whose title+org match existing rows update the row.
        - New definitions create new ScheduledAgentTask rows.
        - Existing rows not present in the YAML are marked disabled (soft).
        """
        if not self._loaded:
            self.load_definitions()

        created: list[str] = []
        updated: list[str] = []
        disabled: list[str] = []
        errors: list[str] = []

        for org_id_str, definitions in self._loaded.items():
            org_id_cast = org_override if org_override is not None else org_id_str

            # Track which definitions we've seen
            seen_keys: set[str] = set()

            for defn in definitions:
                seen_keys.add(defn._definition_key)
                user_id_cast = user_override if user_override is not None else defn.user_id

                if not defn.title or not defn.prompt:
                    errors.append(f"Skipped definition: missing title or prompt")
                    continue

                try:
                    # Compute next run
                    policy = defn.__class__  # placeholder
                    now = datetime.now(timezone.utc)
                    if defn.run_at:
                        run_at = datetime.fromisoformat(defn.run_at).astimezone(timezone.utc)
                    else:
                        # Build a minimal policy dict for next_occurrence
                        policy_dict = self._build_schedule_policy(defn, now)
                        try:
                            run_at = next_occurrence(policy_dict, now)
                        except Exception:
                            run_at = now

                    # Look for existing schedule with same title in same org
                    existing = await session.scalar(
                        select(ScheduledAgentTask).where(
                            ScheduledAgentTask.organization_id == org_id_cast,
                            ScheduledAgentTask.title == defn.title,
                            ScheduledAgentTask.schedule_type == defn.schedule_type,
                        ).order_by(ScheduledAgentTask.created_at.desc())
                    )

                    autonomy = defn.autonomy_policy or {"mode": "approval_required"}

                    if existing:
                        # Update if the definition changed or is re-enabled
                        existing.prompt = defn.prompt
                        existing.schedule_type = defn.schedule_type
                        existing.timezone = defn.timezone
                        existing.schedule_policy = self._build_schedule_policy(defn, run_at)
                        existing.autonomy_policy = autonomy
                        existing.enabled = defn.enabled
                        if existing.enabled and (existing.next_run_at is None or run_at != existing.run_at):
                            existing.next_run_at = max(run_at, datetime.now(timezone.utc))
                        updated.append(str(existing.id))
                    else:
                        schedule = ScheduledAgentTask(
                            organization_id=org_id_cast,
                            user_id=user_id_cast if user_id_cast else None,
                            title=defn.title,
                            prompt=defn.prompt,
                            schedule_type=defn.schedule_type,
                            timezone=defn.timezone,
                            schedule_policy=self._build_schedule_policy(defn, run_at),
                            run_at=run_at,
                            next_run_at=run_at if defn.enabled else None,
                            enabled=defn.enabled,
                            failure_count=0,
                            autonomy_policy=autonomy,
                            created_at=datetime.now(timezone.utc),
                            updated_at=datetime.now(timezone.utc),
                        )
                        session.add(schedule)
                        created.append(defn.title)

                except Exception as exc:
                    errors.append(f"{defn.title}: {str(exc)[:120]}")
                    logger.error("schedule_loader.reconcile_error", title=defn.title, error=str(exc))

            await session.commit()

        # Disable existing schedules not in YAML (soft delete)
        # Only if definitions_dir is actually being used (not just empty)
        if self._loaded:
            from sqlalchemy import func
            all_org_ids = list(self._loaded.keys())
            for org_str in all_org_ids:
                existing_schedules = list((await session.scalars(
                    select(ScheduledAgentTask)
                    .where(
                        ScheduledAgentTask.organization_id == org_id_cast,
                        ScheduledAgentTask.enabled.is_(True),
                    )
                )).all())
                for sched in existing_schedules:
                    if sched.title not in seen_keys:
                        sched.enabled = False
                        sched.next_run_at = None
                        disabled.append(sched.title)
            if disabled:
                await session.commit()

        return {
            "created": created,
            "updated": updated,
            "disabled": disabled,
            "errors": errors,
        }

    def reload(self) -> None:
        """Reload definitions from disk."""
        self._loaded.clear()
        self.load_definitions()


# Module-level singleton
_definition_loader: Optional[ScheduledDefinitionLoader] = None


def get_schedule_def_loader() -> ScheduledDefinitionLoader:
    """Get the singleton schedule definition loader."""
    global _definition_loader
    if _definition_loader is None:
        _definition_loader = ScheduledDefinitionLoader()
    return _definition_loader
