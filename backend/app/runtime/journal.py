"""Append-only, resumable run journal. State derived by replaying events."""
from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import structlog
from sqlalchemy import DateTime, Integer, String, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = structlog.get_logger("onsite48.runtime.journal")

EVENT_TYPES = frozenset({
    "run_started", "model_request", "model_response", "tool_call_proposed", "policy_decision",
    "tool_call_started", "tool_call_result", "readback_result", "approval_requested",
    "approval_resolved", "context_compacted", "run_finished", "run_failed", "nudge", "auth_refresh", "policy_decision",
    "loop_detected", "budget_exhausted",
})

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

try:
    from app.db import Base as _AppBase
    Base = _AppBase
except Exception:
    class Base(DeclarativeBase):
        pass

class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_run_events_run_seq"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    parent_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    trace_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

@dataclass
class RunState:
    run_id: str
    goal: str = ""
    status: str = "running"
    parent_run_id: str | None = None
    seq: int = 0
    messages: list[dict[str, Any]] = field(default_factory=list)
    pending_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    last_tool_started: dict[str, Any] | None = None
    approval_pending: dict[str, Any] | None = None
    finish_payload: dict[str, Any] | None = None
    step_count: int = 0
    token_count: int = 0
    cost_usd: float = 0.0
    todo_md: str = ""
    workspace_files: list[str] = field(default_factory=list)
    user_id: str | None = None
    task_id: str | None = None
    org_id: str | None = None
    role_config: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    recent_calls: list[tuple[str, str]] = field(default_factory=list)
    no_progress_steps: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "goal": self.goal, "status": self.status, "seq": self.seq,
                "step_count": self.step_count, "token_count": self.token_count, "cost_usd": self.cost_usd,
                "todo_md": self.todo_md, "user_id": self.user_id, "task_id": self.task_id, "org_id": self.org_id,
                "approval_pending": self.approval_pending, "finish_payload": self.finish_payload, "error": self.error}

class Journal:
    _memory: dict[str, list] = {}

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None):
        self._sf = session_factory

    def set_session_factory(self, sf: async_sessionmaker[AsyncSession]) -> None:
        self._sf = sf

    async def append(self, run_id: str, event_type: str, payload: dict[str, Any], *, parent_run_id: str | None = None, trace_id: str | None = None, seq: int | None = None) -> int:
        if self._sf is None:
            return self._memory_append(run_id, event_type, payload, parent_run_id, trace_id, seq)
        async with self._sf() as session:
            if seq is None:
                result = await session.execute(select(RunEvent.seq).where(RunEvent.run_id == uuid.UUID(run_id)).order_by(RunEvent.seq.desc()).limit(1))
                last = result.scalar_one_or_none()
                seq = (last or 0) + 1
            row = RunEvent(id=uuid.uuid4(), run_id=uuid.UUID(run_id), parent_run_id=uuid.UUID(parent_run_id) if parent_run_id else None, seq=seq, type=event_type, payload=payload, trace_id=trace_id, created_at=utcnow())
            session.add(row)
            await session.commit()
            return seq

    def _event_to_dict(self, ev: Any) -> dict[str, Any]:
        """Normalize ORM / memory events to a stable dict shape for callers."""
        if isinstance(ev, dict):
            et = ev.get("type") or ev.get("event_type") or ""
            return {
                "seq": int(ev.get("seq") or 0),
                "type": et,
                "event_type": et,
                "payload": ev.get("payload") if isinstance(ev.get("payload"), dict) else (ev.get("payload") or {}),
                "trace_id": ev.get("trace_id"),
                "created_at": ev.get("created_at"),
                "parent_run_id": ev.get("parent_run_id"),
            }
        et = getattr(ev, "type", None) or getattr(ev, "event_type", None) or ""
        return {
            "seq": int(getattr(ev, "seq", 0) or 0),
            "type": et,
            "event_type": et,
            "payload": getattr(ev, "payload", None) if isinstance(getattr(ev, "payload", None), dict) else (getattr(ev, "payload", None) or {}),
            "trace_id": getattr(ev, "trace_id", None),
            "created_at": getattr(ev, "created_at", None),
            "parent_run_id": getattr(ev, "parent_run_id", None),
        }

    async def list_events(self, run_id: str) -> list[dict[str, Any]]:
        if self._sf is None:
            return [self._event_to_dict(e) for e in self._memory.get(run_id, [])]
        async with self._sf() as session:
            result = await session.execute(
                select(RunEvent).where(RunEvent.run_id == uuid.UUID(run_id)).order_by(RunEvent.seq.asc())
            )
            return [self._event_to_dict(row) for row in result.scalars().all()]

    async def replay(self, run_id: str, *, goal: str = "", role_config: dict | None = None) -> RunState:
        events = await self.list_events(run_id)
        state = RunState(run_id=run_id, goal=goal, role_config=role_config or {})
        for ev in events:
            et = ev.get("type") or ev.get("event_type") or ""
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            self._apply(state, et, payload, int(ev.get("seq") or 0))
        return state

    def _apply(self, state: RunState, event_type: str, payload: dict[str, Any], seq: int) -> None:
        state.seq = max(state.seq, seq)
        if event_type == "run_started":
            state.goal = payload.get("goal") or state.goal
            state.user_id = payload.get("user_id") or state.user_id
            state.task_id = payload.get("task_id") or state.task_id
            state.org_id = payload.get("org_id") or state.org_id
            state.role_config = payload.get("role_config") or state.role_config
            state.status = "running"
        elif event_type == "model_response":
            state.step_count += 1
            state.token_count += int(payload.get("tokens") or 0)
            state.cost_usd += float(payload.get("cost_usd") or 0)
            if payload.get("message"):
                state.messages.append(payload["message"])
        elif event_type == "tool_call_proposed":
            state.pending_tool_calls.append(payload)
        elif event_type == "tool_call_started":
            state.last_tool_started = payload
            state.pending_tool_calls = [c for c in state.pending_tool_calls if c.get("call_id") != payload.get("call_id")]
        elif event_type == "tool_call_result":
            state.last_tool_started = None
            if payload.get("message"):
                state.messages.append(payload["message"])
            state.no_progress_steps = 0
        elif event_type == "approval_requested":
            state.status = "waiting_approval"
            state.approval_pending = payload
        elif event_type == "approval_resolved":
            state.approval_pending = None
            state.status = "running" if payload.get("approved") else "failed"
            if not payload.get("approved"):
                state.error = {"error_code": "approval_denied", "message": payload.get("reason") or "denied"}
        elif event_type == "run_finished":
            state.status = "finished"
            state.finish_payload = payload
        elif event_type in ("run_failed", "budget_exhausted"):
            state.status = "failed"
            state.error = payload

    def _memory_append(self, run_id, event_type, payload, parent_run_id, trace_id, seq):
        bucket = self._memory.setdefault(run_id, [])
        if bucket:
            last = bucket[-1]
            last_seq = last.get("seq") if isinstance(last, dict) else getattr(last, "seq", 0)
        else:
            last_seq = 0
        next_seq = (last_seq or 0) + 1 if seq is None else seq
        bucket.append({
            "seq": next_seq,
            "type": event_type,
            "event_type": event_type,
            "payload": payload if isinstance(payload, dict) else {},
            "parent_run_id": parent_run_id,
            "trace_id": trace_id,
            "created_at": utcnow().isoformat(),
        })
        return next_seq

journal = Journal()
