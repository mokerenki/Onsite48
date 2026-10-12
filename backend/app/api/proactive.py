"""Authenticated proactive operations and marketing approval APIs."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from zoneinfo import ZoneInfo
import secrets
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.files import workspace_membership
from app.core.security import get_current_user, get_db_session
from app.models import ActionProposal, AutomationSubscription, AutomationTrigger, MarketingCampaign, OrganizationMember, ProposalStatus, ProactiveSignal, ProactiveSignalStatus, ScheduledAgentTask, ScheduledTaskExecution, User
from app.services.heartbeat_service import HeartbeatObservation, heartbeat_service
from app.services.proactive_operations import build_morning_briefing, create_proposal
from app.services.recurrence import next_occurrence

router = APIRouter(prefix="/api/proactive", tags=["proactive operations"])


class ScheduleRequest(BaseModel):
    enabled: bool = True
    timezone: str = Field(default="UTC", max_length=64)
    hour: int = Field(default=8, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)


class ScheduleAutonomyPolicy(BaseModel):
    """Explicit user delegation for one durable scheduled workflow."""

    mode: Literal["approval_required", "autonomous"] = "autonomous"
    allow_all: bool = False
    additional_allowed_tools: list[str] = Field(default_factory=list, max_length=40)
    allowed_custom_mcp_tools: list[str] = Field(default_factory=list, max_length=80)

    @staticmethod
    def _clean(values: list[str]) -> list[str]:
        return sorted({value.strip() for value in values if value.strip()})

    def durable_value(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "allow_all": self.allow_all,
            "additional_allowed_tools": self._clean(self.additional_allowed_tools),
            "allowed_custom_mcp_tools": self._clean(self.allowed_custom_mcp_tools),
        }


class ScheduledAgentTaskRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    prompt: str = Field(min_length=1, max_length=12000)
    schedule_type: Literal["once", "daily", "weekly", "monthly"] = "once"
    # New clients submit a wall-clock date/time and an IANA timezone. ``run_at``
    # remains accepted for existing callers that already send an aware instant.
    local_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    local_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    run_at: datetime | None = None
    timezone: str = Field(default="UTC", max_length=64)
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    month_days: list[int] = Field(default_factory=list, max_length=31)
    quiet_hours: dict[str, Any] = Field(default_factory=dict)
    business_hours: dict[str, Any] = Field(default_factory=dict)
    calendar_policy: dict[str, Any] = Field(default_factory=dict)
    autonomy_policy: ScheduleAutonomyPolicy = Field(default_factory=ScheduleAutonomyPolicy)

    def schedule_policy(self, run_at: datetime) -> dict[str, Any]:
        if self.schedule_type == "weekly" and not self.weekdays:
            raise ValueError("Weekly schedules require at least one weekday.")
        if self.schedule_type == "monthly" and not self.month_days:
            raise ValueError("Monthly schedules require at least one month day.")
        if any(day < 1 or day > 7 for day in self.weekdays):
            raise ValueError("Weekdays must use ISO values from 1 through 7.")
        if any(day < 1 or day > 31 for day in self.month_days):
            raise ValueError("Month days must be between 1 and 31.")
        try:
            local_time = self.local_time or run_at.astimezone(ZoneInfo(self.timezone)).strftime("%H:%M")
        except Exception as exc:
            raise ValueError("The schedule timezone is invalid.") from exc
        return {
            "version": 1, "frequency": self.schedule_type, "timezone": self.timezone,
            "local_time": local_time, "weekdays": sorted(set(self.weekdays)),
            "month_days": sorted(set(self.month_days)), "run_at": run_at.isoformat(),
            "quiet_hours": self.quiet_hours, "business_hours": self.business_hours,
            "calendar_policy": self.calendar_policy,
        }


class ChatAutomationIntentRequest(BaseModel):
    message: str = Field(min_length=8, max_length=12000)
    timezone: str = Field(default="UTC", max_length=64)
    confirm: bool = False


def parse_chat_automation(message: str, timezone_name: str) -> dict[str, Any]:
    text = message.strip()
    match = re.search(r"every\s+(day|daily|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+at\s+(\d{1,2}:\d{2})\s*(?:,|-)\s*(.+)$", text, re.I)
    if not match:
        raise HTTPException(status_code=422, detail="Use a schedule such as: Every Monday at 08:00, give me a weekly market update.")
    frequency_word, local_time, prompt = match.groups()
    day_map = {"monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6, "sunday": 7}
    frequency = "daily" if frequency_word.lower() in {"day", "daily"} else "weekly"
    policy = {"version": 1, "frequency": frequency, "timezone": timezone_name, "local_time": local_time, "weekdays": [day_map[frequency_word.lower()]] if frequency == "weekly" else [], "month_days": [], "quiet_hours": {}, "business_hours": {}, "calendar_policy": {}}
    try:
        run_at = next_occurrence(policy, datetime.now(timezone.utc))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not calculate the next occurrence: {exc}") from exc
    return {"title": prompt.strip()[:240], "prompt": prompt.strip(), "schedule_type": frequency, "timezone": timezone_name, "run_at": run_at, "policy": policy}


class CampaignCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    objective: str = Field(min_length=1, max_length=8000)
    channel: str | None = Field(default=None, max_length=100)
    owner_user_id: str | None = None
    due_at: datetime | None = None


class CampaignUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    objective: str | None = Field(default=None, min_length=1, max_length=8000)
    channel: str | None = Field(default=None, max_length=100)
    status: Literal["backlog", "researching", "in_review", "approved", "live", "complete"] | None = None
    owner_user_id: str | None = None
    due_at: datetime | None = None


class SignalRequest(BaseModel):
    source: str = Field(max_length=100)
    signal_type: str = Field(max_length=100)
    title: str = Field(max_length=300)
    summary: str = Field(max_length=8000)
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    confidence: int = Field(default=50, ge=0, le=100)
    evidence: dict[str, Any] = Field(default_factory=dict)
    entity_refs: list[str] = Field(default_factory=list, max_length=50)
    recommended_playbook: str | None = Field(default=None, max_length=160)
    disposition: Literal["observe", "recommend", "delegate"] = "observe"


class TriggerCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    default_severity: Literal["low", "medium", "high", "critical"] = "medium"


class ProposalRequest(BaseModel):
    title: str = Field(max_length=300)
    rationale: str = Field(max_length=8000)
    action_type: str = Field(max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    connector_id: str | None = Field(default=None, max_length=100)
    signal_id: str | None = None


async def owned_record(session: AsyncSession, model: Any, record_id: str, user: User, organization_id: Any) -> Any:
    record = await session.get(model, record_id)
    if record is None or record.user_id != user.id or record.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found.")
    return record


def campaign_summary(item: MarketingCampaign, owners: dict[Any, dict[str, str]]) -> dict[str, Any]:
    return {
        "id": str(item.id), "title": item.title, "objective": item.objective, "channel": item.channel,
        "status": item.status, "owner_user_id": str(item.owner_user_id) if item.owner_user_id else None,
        "owner": owners.get(item.owner_user_id) if item.owner_user_id else None,
        "created_by_user_id": str(item.created_by_user_id), "due_at": item.due_at,
        "created_at": item.created_at, "updated_at": item.updated_at,
    }


def execution_summary(item: ScheduledTaskExecution) -> dict[str, Any]:
    return {
        "id": str(item.id), "schedule_id": str(item.schedule_id), "task_thread_id": item.task_thread_id,
        "attempt": item.attempt, "status": item.status, "scheduled_for": item.scheduled_for,
        "started_at": item.started_at, "heartbeat_at": item.heartbeat_at,
        "lease_expires_at": item.lease_expires_at, "completed_at": item.completed_at,
        "error": item.error, "summary": item.summary,
    }


def scheduled_run_at(payload: ScheduledAgentTaskRequest) -> datetime:
    """Return the requested UTC instant without treating local wall time as UTC."""
    if payload.local_date is not None or payload.local_time is not None:
        if not payload.local_date or not payload.local_time:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provide both local_date and local_time for a scheduled task.")
        try:
            zone = ZoneInfo(payload.timezone)
            naive = datetime.fromisoformat(f"{payload.local_date}T{payload.local_time}:00")
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The selected schedule date, time, or timezone is invalid.") from exc
        localized = naive.replace(tzinfo=zone)
        # ZoneInfo normalizes skipped DST wall times. Reject them instead of
        # silently scheduling a different instant from the one the user chose.
        if localized.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != naive:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The selected local time does not exist in the chosen timezone.")
        return localized.astimezone(timezone.utc)
    if payload.run_at is None or payload.run_at.tzinfo is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provide local date/time with timezone or an aware run_at instant.")
    return payload.run_at.astimezone(timezone.utc)


@router.get("/overview")
async def overview(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    membership = await workspace_membership(session, user)
    signals = list((await session.scalars(
        select(ProactiveSignal).where(ProactiveSignal.organization_id == membership.organization_id, ProactiveSignal.user_id == user.id, ProactiveSignal.status == ProactiveSignalStatus.OPEN).order_by(ProactiveSignal.detected_at.desc()).limit(30)
    )).all())
    proposals = list((await session.scalars(
        select(ActionProposal).where(ActionProposal.organization_id == membership.organization_id, ActionProposal.user_id == user.id, ActionProposal.status == ProposalStatus.PENDING).order_by(ActionProposal.created_at.desc()).limit(30)
    )).all())
    subscription = await session.scalar(select(AutomationSubscription).where(AutomationSubscription.organization_id == membership.organization_id, AutomationSubscription.user_id == user.id, AutomationSubscription.kind == "marketing_morning_briefing"))
    scheduled = list((await session.scalars(select(ScheduledAgentTask).where(
        ScheduledAgentTask.organization_id == membership.organization_id,
        ScheduledAgentTask.user_id == user.id,
    ).order_by(ScheduledAgentTask.next_run_at.asc().nullslast(), ScheduledAgentTask.created_at.desc()).limit(50))).all())
    schedule_ids = [item.id for item in scheduled]
    execution_by_schedule: dict[Any, list[dict[str, Any]]] = {schedule_id: [] for schedule_id in schedule_ids}
    if schedule_ids:
        executions = list((await session.scalars(select(ScheduledTaskExecution).where(
            ScheduledTaskExecution.organization_id == membership.organization_id,
            ScheduledTaskExecution.user_id == user.id,
            ScheduledTaskExecution.schedule_id.in_(schedule_ids),
        ).order_by(ScheduledTaskExecution.started_at.desc()).limit(150))).all())
        for execution in executions:
            rows = execution_by_schedule.get(execution.schedule_id)
            if rows is not None and len(rows) < 5:
                rows.append(execution_summary(execution))
    members = list((await session.execute(select(OrganizationMember, User).join(User, OrganizationMember.user_id == User.id).where(
        OrganizationMember.organization_id == membership.organization_id,
        User.is_active.is_(True),
    ))).all())
    owners = {member.user_id: {"id": str(person.id), "display_name": person.display_name, "email": person.email, "role": str(member.role)} for member, person in members}
    campaigns = list((await session.scalars(select(MarketingCampaign).where(
        MarketingCampaign.organization_id == membership.organization_id,
    ).order_by(MarketingCampaign.updated_at.desc()).limit(100))).all())
    return {
        "signals": [{"id": str(item.id), "title": item.title, "summary": item.summary, "severity": item.severity, "source": item.source, "detected_at": item.detected_at} for item in signals],
        "proposals": [{"id": str(item.id), "title": item.title, "rationale": item.rationale, "action_type": item.action_type, "connector_id": item.connector_id, "created_at": item.created_at} for item in proposals],
        "campaigns": [campaign_summary(item, owners) for item in campaigns],
        "members": list(owners.values()),
        "scheduled_tasks": [{
            "id": str(item.id), "title": item.title, "prompt": item.prompt,
            "schedule_type": item.schedule_type, "timezone": item.timezone,
            "schedule_policy": item.schedule_policy or {},
            "run_at": item.run_at, "next_run_at": item.next_run_at,
            "enabled": item.enabled, "last_run_at": item.last_run_at,
            "last_task_thread_id": item.last_task_thread_id, "failure_count": item.failure_count,
            "last_error": item.last_error, "autonomy_policy": item.autonomy_policy or {"mode": "approval_required"},
            "executions": execution_by_schedule.get(item.id, []),
        } for item in scheduled],
        "schedule": ({
            "enabled": subscription.enabled,
            "timezone": subscription.timezone,
            **subscription.schedule,
            "last_run_at": subscription.last_run_at,
            "next_retry_at": subscription.next_run_at,
            "failure_count": subscription.failure_count,
            "last_error": subscription.last_error,
            "last_error_at": subscription.last_error_at,
            "dead_lettered_at": subscription.dead_lettered_at,
        } if subscription else None),
    }


async def _workspace_owner(session: AsyncSession, organization_id: Any, owner_user_id: str | None) -> Any | None:
    if owner_user_id is None:
        return None
    member = await session.scalar(select(OrganizationMember).where(
        OrganizationMember.organization_id == organization_id,
        OrganizationMember.user_id == owner_user_id,
    ))
    if member is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Campaign owner must belong to this workspace.")
    return member.user_id


@router.get("/workspace-members")
async def workspace_members(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    membership = await workspace_membership(session, user)
    rows = list((await session.execute(select(OrganizationMember, User).join(User, OrganizationMember.user_id == User.id).where(
        OrganizationMember.organization_id == membership.organization_id,
        User.is_active.is_(True),
    ).order_by(User.display_name.asc()))).all())
    return {"members": [{"id": str(person.id), "display_name": person.display_name, "email": person.email, "role": str(member.role)} for member, person in rows]}


@router.post("/campaigns", status_code=status.HTTP_201_CREATED)
async def create_campaign(payload: CampaignCreateRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    membership = await workspace_membership(session, user)
    owner = await _workspace_owner(session, membership.organization_id, payload.owner_user_id)
    campaign = MarketingCampaign(
        organization_id=membership.organization_id, created_by_user_id=user.id, owner_user_id=owner,
        title=payload.title.strip(), objective=payload.objective.strip(), channel=(payload.channel or "").strip() or None,
        due_at=payload.due_at.astimezone(timezone.utc) if payload.due_at and payload.due_at.tzinfo else payload.due_at,
    )
    session.add(campaign)
    await session.commit()
    await session.refresh(campaign)
    owners = {user.id: {"id": str(user.id), "display_name": user.display_name, "email": user.email, "role": str(membership.role)}}
    if owner and owner != user.id:
        person = await session.get(User, owner)
        owner_membership = await session.scalar(select(OrganizationMember).where(OrganizationMember.organization_id == membership.organization_id, OrganizationMember.user_id == owner))
        if person and owner_membership:
            owners[owner] = {"id": str(person.id), "display_name": person.display_name, "email": person.email, "role": str(owner_membership.role)}
    return campaign_summary(campaign, owners)


@router.patch("/campaigns/{campaign_id}")
async def update_campaign(campaign_id: str, payload: CampaignUpdateRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    membership = await workspace_membership(session, user)
    campaign = await session.get(MarketingCampaign, campaign_id)
    if campaign is None or campaign.organization_id != membership.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")
    if payload.title is not None: campaign.title = payload.title.strip()
    if payload.objective is not None: campaign.objective = payload.objective.strip()
    if payload.channel is not None: campaign.channel = payload.channel.strip() or None
    if payload.status is not None: campaign.status = payload.status
    if "owner_user_id" in payload.model_fields_set: campaign.owner_user_id = await _workspace_owner(session, membership.organization_id, payload.owner_user_id)
    if payload.due_at is not None: campaign.due_at = payload.due_at.astimezone(timezone.utc) if payload.due_at.tzinfo else payload.due_at
    await session.commit()
    await session.refresh(campaign)
    members = list((await session.execute(select(OrganizationMember, User).join(User, OrganizationMember.user_id == User.id).where(OrganizationMember.organization_id == membership.organization_id))).all())
    owners = {member.user_id: {"id": str(person.id), "display_name": person.display_name, "email": person.email, "role": str(member.role)} for member, person in members}
    return campaign_summary(campaign, owners)


@router.put("/morning-briefing/schedule")
async def configure_schedule(payload: ScheduleRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    membership = await workspace_membership(session, user)
    subscription = await session.scalar(select(AutomationSubscription).where(AutomationSubscription.organization_id == membership.organization_id, AutomationSubscription.user_id == user.id, AutomationSubscription.kind == "marketing_morning_briefing"))
    if subscription is None:
        subscription = AutomationSubscription(organization_id=membership.organization_id, user_id=user.id, kind="marketing_morning_briefing")
        session.add(subscription)
    subscription.enabled = payload.enabled
    subscription.timezone = payload.timezone
    subscription.schedule = {"hour": payload.hour, "minute": payload.minute}
    # Saving a schedule is the explicit operator recovery action for a dead-lettered job.
    subscription.failure_count = 0
    subscription.last_error = None
    subscription.last_error_at = None
    subscription.dead_lettered_at = None
    subscription.next_run_at = None
    await session.commit()
    return {"enabled": subscription.enabled, "timezone": subscription.timezone, **subscription.schedule, "failure_count": subscription.failure_count}


@router.post("/scheduled-tasks", status_code=status.HTTP_201_CREATED)
async def create_scheduled_task(payload: ScheduledAgentTaskRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    membership = await workspace_membership(session, user)
    run_at = scheduled_run_at(payload)
    if run_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Choose a future date and time.")
    try:
        schedule_policy = payload.schedule_policy(run_at)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    task = ScheduledAgentTask(
        organization_id=membership.organization_id,
        user_id=user.id,
        title=payload.title.strip(),
        prompt=payload.prompt.strip(),
        schedule_type=payload.schedule_type,
        timezone=payload.timezone,
        schedule_policy=schedule_policy,
        run_at=run_at,
        next_run_at=run_at,
        autonomy_policy=payload.autonomy_policy.durable_value(),
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return {
        "id": str(task.id), "title": task.title, "schedule_type": task.schedule_type,
        "run_at": task.run_at, "next_run_at": task.next_run_at, "enabled": task.enabled,
        "schedule_policy": task.schedule_policy,
        "autonomy_policy": task.autonomy_policy,
    }


@router.put("/scheduled-tasks/{schedule_id}/autonomy-policy")
async def update_scheduled_task_autonomy_policy(
    schedule_id: str,
    payload: ScheduleAutonomyPolicy,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    membership = await workspace_membership(session, user)
    task = await owned_record(session, ScheduledAgentTask, schedule_id, user, membership.organization_id)
    task.autonomy_policy = payload.durable_value()
    await session.commit()
    return {"id": str(task.id), "autonomy_policy": task.autonomy_policy}


@router.get("/scheduled-tasks/{schedule_id}/executions")
async def list_scheduled_task_executions(schedule_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    membership = await workspace_membership(session, user)
    await owned_record(session, ScheduledAgentTask, schedule_id, user, membership.organization_id)
    executions = list((await session.scalars(select(ScheduledTaskExecution).where(
        ScheduledTaskExecution.schedule_id == schedule_id,
        ScheduledTaskExecution.organization_id == membership.organization_id,
        ScheduledTaskExecution.user_id == user.id,
    ).order_by(ScheduledTaskExecution.started_at.desc()).limit(100))).all())
    return {"executions": [execution_summary(item) for item in executions]}


@router.post("/scheduled-tasks/{schedule_id}/retry")
async def retry_scheduled_task(schedule_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    membership = await workspace_membership(session, user)
    task = await owned_record(session, ScheduledAgentTask, schedule_id, user, membership.organization_id)
    task.enabled = True
    task.next_run_at = datetime.now(timezone.utc)
    task.failure_count = 0
    task.last_error = None
    await session.commit()
    return {"id": str(task.id), "enabled": task.enabled, "next_run_at": task.next_run_at, "notice": "Retry queued. It will run when the proactive worker claims the schedule."}


@router.get("/worker-health")
async def scheduled_worker_health(request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    await workspace_membership(session, user)
    redis_client = getattr(request.app.state, "redis_client", None)
    heartbeat: str | None = None
    if redis_client is not None:
        raw = await redis_client.get("onsite48:proactive_worker:heartbeat")
        heartbeat = raw.decode() if isinstance(raw, bytes) else raw
    process_worker = getattr(request.app.state, "proactive_worker", None)
    return {
        "healthy": bool(heartbeat),
        "heartbeat_at": heartbeat,
        "mode": "api_process" if process_worker is not None else "separate_worker",
        "process_status": process_worker.status() if process_worker is not None else None,
        "notice": "The worker heartbeat expires automatically if the scheduler stops. A missing heartbeat means scheduled tasks will remain queued until the worker is restored.",
    }


@router.delete("/scheduled-tasks/{schedule_id}")
async def cancel_scheduled_task(schedule_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    membership = await workspace_membership(session, user)
    task = await owned_record(session, ScheduledAgentTask, schedule_id, user, membership.organization_id)
    task.enabled = False
    task.next_run_at = None
    await session.commit()
    return {"id": str(task.id), "enabled": False}


@router.get("/triggers")
async def list_triggers(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    membership = await workspace_membership(session, user)
    triggers = list((await session.scalars(select(AutomationTrigger).where(
        AutomationTrigger.organization_id == membership.organization_id,
        AutomationTrigger.user_id == user.id,
    ).order_by(AutomationTrigger.updated_at.desc()))).all())
    return {"triggers": [{
        "id": str(item.id), "name": item.name, "trigger_type": item.trigger_type, "enabled": item.enabled,
        "configuration": item.configuration, "last_received_at": item.last_received_at,
    } for item in triggers]}


@router.post("/triggers", status_code=status.HTTP_201_CREATED)
async def create_trigger(payload: TriggerCreateRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    membership = await workspace_membership(session, user)
    raw_secret = secrets.token_urlsafe(32)
    trigger = AutomationTrigger(
        organization_id=membership.organization_id,
        user_id=user.id,
        name=payload.name.strip(),
        trigger_type="webhook",
        enabled=True,
        secret_hash=hashlib.sha256(raw_secret.encode("utf-8")).hexdigest(),
        configuration={"default_severity": payload.default_severity},
    )
    session.add(trigger)
    try:
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A trigger with that name already exists.") from exc
    return {
        "id": str(trigger.id), "name": trigger.name, "enabled": trigger.enabled,
        "secret": raw_secret,
        "endpoint": f"/api/proactive/triggers/{trigger.id}/ingest",
        "notice": "Store this secret in the sender only. Onsite48 displays it once and stores only a hash.",
    }


@router.post("/triggers/{trigger_id}/ingest")
async def ingest_trigger(
    trigger_id: str,
    payload: dict[str, Any],
    x_synth_trigger_secret: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    trigger = await session.get(AutomationTrigger, trigger_id)
    provided = hashlib.sha256(x_synth_trigger_secret.encode("utf-8")).hexdigest() if x_synth_trigger_secret else ""
    if trigger is None or not trigger.enabled or not secrets.compare_digest(trigger.secret_hash, provided):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Trigger authentication failed.")
    title = str(payload.get("title") or f"Event received by {trigger.name}")[:300]
    summary = str(payload.get("summary") or payload.get("message") or "A configured proactive trigger received an event.")[:8000]
    requested_severity = str(payload.get("severity") or trigger.configuration.get("default_severity", "medium"))
    severity = requested_severity if requested_severity in {"low", "medium", "high", "critical"} else "medium"
    raw_entity_refs = payload.get("entity_refs", [])
    entity_refs = tuple(str(item)[:240] for item in raw_entity_refs if isinstance(item, (str, int, float))) if isinstance(raw_entity_refs, list) else ()
    signal = await heartbeat_service.record_observation(
        session,
        organization_id=trigger.organization_id,
        user_id=trigger.user_id,
        observation=HeartbeatObservation(
            source=f"trigger:{trigger.name}",
            signal_type=str(payload.get("type") or "webhook_event")[:100],
            title=title,
            summary=summary,
            severity=severity,
            confidence=max(0, min(100, int(payload.get("confidence", 70)))),
            evidence={"trigger_id": str(trigger.id), "payload": payload},
            entity_refs=entity_refs,
            recommended_playbook=str(payload.get("recommended_playbook") or "")[:160] or None,
            disposition=str(payload.get("disposition") or "observe") if str(payload.get("disposition") or "observe") in {"observe", "recommend", "delegate"} else "observe",
        ),
    )
    trigger.last_received_at = datetime.now(timezone.utc)
    await session.commit()
    return {"accepted": True, "signal_id": str(signal.id)}


@router.post("/signals")
async def ingest_signal(payload: SignalRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    membership = await workspace_membership(session, user)
    signal = await heartbeat_service.record_observation(
        session,
        organization_id=membership.organization_id,
        user_id=user.id,
        observation=HeartbeatObservation(**payload.model_dump()),
    )
    await session.commit()
    return {"id": str(signal.id), "status": signal.status}


@router.post("/proposals")
async def propose_action(payload: ProposalRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    membership = await workspace_membership(session, user)
    proposal = await create_proposal(session, organization_id=membership.organization_id, user_id=user.id, **payload.model_dump())
    await session.commit()
    return {"id": str(proposal.id), "status": proposal.status}


@router.post("/briefings/run")
async def run_briefing_now(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    membership = await workspace_membership(session, user)
    briefing = await build_morning_briefing(session, organization_id=membership.organization_id, user_id=user.id)
    await session.commit()
    return {"id": str(briefing.id), "summary": briefing.summary, "signal_count": briefing.signal_count, "proposal_count": briefing.proposal_count}


@router.post("/proposals/{proposal_id}/decision")
async def decide_proposal(proposal_id: str, approved: bool, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    membership = await workspace_membership(session, user)
    proposal = await owned_record(session, ActionProposal, proposal_id, user, membership.organization_id)
    if proposal.status != ProposalStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This proposal has already been decided.")
    proposal.status = ProposalStatus.APPROVED if approved else ProposalStatus.REJECTED
    proposal.approved_by_user_id = user.id
    proposal.decided_at = datetime.now(timezone.utc)
    await session.commit()
    return {"id": str(proposal.id), "status": proposal.status}