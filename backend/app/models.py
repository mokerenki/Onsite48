"""Persistent product models for Onsite48.

The database is the source of truth for user ownership, task visibility, approval
records, and connector metadata. OAuth secrets are encrypted before persistence.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class UserRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ConnectorAuthType(StrEnum):
    OAUTH2 = "oauth2"
    API_KEY = "api_key"


class ProactiveSignalStatus(StrEnum):
    OPEN = "open"
    DISMISSED = "dismissed"
    RESOLVED = "resolved"


class ProposalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


class ConnectorStatus(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    ERROR = "error"
    EXPIRED = "expired"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    memberships: Mapped[list[OrganizationMember]] = relationship(back_populates="user", cascade="all, delete-orphan")
    sessions: Mapped[list[UserSession]] = relationship(back_populates="user", cascade="all, delete-orphan")
    oauth_accounts: Mapped[list[OAuthAccount]] = relationship(back_populates="user", cascade="all, delete-orphan")
    tasks: Mapped[list[TaskThread]] = relationship(back_populates="user", cascade="all, delete-orphan")
    connector_connections: Mapped[list[ConnectorConnection]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    memberships: Mapped[list[OrganizationMember]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    tasks: Mapped[list[TaskThread]] = relationship(back_populates="organization")
    connector_connections: Mapped[list[ConnectorConnection]] = relationship(back_populates="organization")


class OrganizationMember(Base):
    __tablename__ = "organization_members"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_organization_member"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[UserRole] = mapped_column(SqlEnum(UserRole, name="user_role", values_callable=lambda obj: [member.value for member in obj]), default=UserRole.MEMBER, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="sessions")


class OAuthAccount(Base):
    __tablename__ = "oauth_accounts"
    __table_args__ = (UniqueConstraint("provider", "provider_account_id", name="uq_oauth_provider_account"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_access_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    encrypted_refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="oauth_accounts")


class TaskThread(Base):
    __tablename__ = "task_threads"

    id: Mapped[uuid.UUID] = uuid_pk()
    external_thread_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(String(64), default="general", nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default="medium", nullable=False, index=True)
    status: Mapped[TaskStatus] = mapped_column(SqlEnum(TaskStatus, name="task_status", values_callable=lambda obj: [member.value for member in obj]), default=TaskStatus.QUEUED, nullable=False, index=True)
    # Set when accepted work is dispatched and refreshed as the task progresses.
    # A queued task whose lease has expired was dispatched to a process that no
    # longer exists, and is failed visibly by the orphan sweeper.
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    # How many times this task has been dispatched. A lease that lapses means a
    # process went away mid-run; whether to hand the work to another process is a
    # decision that needs a count, and without it the sweeper can only fail the
    # task -- losing work that a single restart would have completed.
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    # A worker must present this token when starting or renewing a claim. It
    # fences a stale worker after the lease is requeued to another worker.
    lease_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    final_answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cost_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    execution_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="tasks")
    organization: Mapped[Organization] = relationship(back_populates="tasks")
    events: Mapped[list[TaskEvent]] = relationship(back_populates="task", cascade="all, delete-orphan", order_by="TaskEvent.sequence")
    approvals: Mapped[list[ToolApproval]] = relationship(back_populates="task", cascade="all, delete-orphan")


class BrowserSession(Base):
    """Task-owned durable browser execution resource (Manus-style workspace).

    New sessions are always task_workspace. The focused_tab mode is retained
    only so existing rows remain readable; it is rejected for new work.
    """
    __tablename__ = "browser_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("task_threads.id", ondelete="SET NULL"), nullable=True, index=True)
    device_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("browser_bridge_devices.id", ondelete="SET NULL"), nullable=True, index=True)
    mode: Mapped[str] = mapped_column(String(24), default="task_workspace", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="authorizing", nullable=False, index=True)
    requested_origins: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    granted_capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    tab_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    primary_tab_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    window_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tab_group_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tab_group_title: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    authorization_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    world_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_step_id: Mapped[Optional[str]] = mapped_column(String(96), nullable=True)
    current_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    current_observation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    authorization_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    stop_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class BrowserAuthorizationRecord(Base):
    """Server-authoritative, task-scoped browser authorization grant."""
    __tablename__ = "browser_authorizations"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("task_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("browser_bridge_devices.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("browser_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    allowed_origins: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    allowed_capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class BrowserSessionEvent(Base):
    __tablename__ = "browser_session_events"
    __table_args__ = (UniqueConstraint("session_id", "sequence", name="uq_browser_session_event_sequence"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("browser_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("task_threads.id", ondelete="SET NULL"), nullable=True, index=True)
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    step_attempt_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("agent_step_attempts.id", ondelete="SET NULL"), nullable=True, index=True)
    bridge_command_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("browser_bridge_commands.id", ondelete="SET NULL"), nullable=True, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (UniqueConstraint("task_id", "sequence", name="uq_task_event_sequence"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("task_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    # The trace that produced this event, so an event in the task timeline can
    # be walked back to the spans and log lines that made it happen. Without it
    # the only way to explain an event is by timestamp.
    trace_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    task: Mapped[TaskThread] = relationship(back_populates="events")


class OutboxEvent(Base):
    """Transactional delivery record for task events and downstream work."""
    __tablename__ = "outbox_events"
    __table_args__ = (UniqueConstraint("event_id", name="uq_outbox_event_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("task_events.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("task_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    topic: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AgentRun(Base):
    """Durable execution envelope for one task attempt or resumed run."""
    __tablename__ = "agent_runs"
    __table_args__ = (UniqueConstraint("task_id", "run_number", name="uq_agent_run_task_number"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("task_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    browser_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("browser_sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    run_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(48), default="queued", nullable=False, index=True)
    intent: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    plan: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    task: Mapped[TaskThread] = relationship()
    attempts: Mapped[list[AgentStepAttempt]] = relationship(back_populates="run", cascade="all, delete-orphan")
    evidence: Mapped[list[AgentEvidence]] = relationship(back_populates="run", cascade="all, delete-orphan")
    artifacts: Mapped[list[AgentArtifact]] = relationship(back_populates="run", cascade="all, delete-orphan")


class AgentStepAttempt(Base):
    """One durable, idempotency-keyed invocation of a plan step."""
    __tablename__ = "agent_step_attempts"
    __table_args__ = (UniqueConstraint("run_id", "idempotency_key", name="uq_agent_attempt_idempotency"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    browser_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("browser_sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    step_id: Mapped[str] = mapped_column(String(96), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(160), nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(48), default="started", nullable=False, index=True)
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[AgentRun] = relationship(back_populates="attempts")


class AgentEvidence(Base):
    """A source-grounded observation attached to a durable run."""
    __tablename__ = "agent_evidence"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    step_id: Mapped[Optional[str]] = mapped_column(String(96), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    run: Mapped[AgentRun] = relationship(back_populates="evidence")


class AgentArtifact(Base):
    """Immutable artifact descriptor; bytes remain in the artifact store."""
    __tablename__ = "agent_artifacts"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(160), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    logical_id: Mapped[str] = mapped_column(String(255), default="", nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    parent_artifact_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("agent_artifacts.id", ondelete="SET NULL"), nullable=True, index=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    run: Mapped[AgentRun] = relationship(back_populates="artifacts")


class ToolApproval(Base):
    __tablename__ = "tool_approvals"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("task_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    decision: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    decided_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    task: Mapped[TaskThread] = relationship(back_populates="approvals")


class ConnectorConnection(Base):
    __tablename__ = "connector_connections"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", "connector_id", name="uq_connector_connection"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    connector_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    auth_type: Mapped[ConnectorAuthType] = mapped_column(SqlEnum(ConnectorAuthType, name="connector_auth_type", values_callable=lambda obj: [member.value for member in obj]), nullable=False)
    status: Mapped[ConnectorStatus] = mapped_column(SqlEnum(ConnectorStatus, name="connector_status", values_callable=lambda obj: [member.value for member in obj]), default=ConnectorStatus.DISCONNECTED, nullable=False)
    encrypted_credentials: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    external_account_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    external_account_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="connector_connections")
    user: Mapped[User] = relationship(back_populates="connector_connections")


class OAuthAuthorizationState(Base):
    __tablename__ = "oauth_authorization_states"

    id: Mapped[uuid.UUID] = uuid_pk()
    state: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    connector_id: Mapped[str] = mapped_column(String(100), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    code_verifier: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    redirect_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AutomationSubscription(Base):
    __tablename__ = "automation_subscriptions"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", "kind", name="uq_automation_subscription_kind"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    schedule: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_lettered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ScheduledAgentTask(Base):
    __tablename__ = "scheduled_agent_tasks"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(24), default="once", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    schedule_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_task_thread_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # The user-owned execution policy is stored with the schedule rather than
    # an ephemeral task checkpoint, making autonomous delegation durable and
    # auditable across every scheduled occurrence.
    autonomy_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ProactiveSignal(Base):
    __tablename__ = "proactive_signals"
    __table_args__ = (UniqueConstraint("organization_id", "deduplication_key", name="uq_proactive_signal_deduplication"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(24), default="medium", nullable=False, index=True)
    confidence: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[ProactiveSignalStatus] = mapped_column(SqlEnum(ProactiveSignalStatus, name="proactive_signal_status", values_callable=lambda obj: [member.value for member in obj]), default=ProactiveSignalStatus.OPEN, nullable=False, index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ActionProposal(Base):
    __tablename__ = "action_proposals"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    signal_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("proactive_signals.id", ondelete="SET NULL"), nullable=True, index=True)
    connector_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[ProposalStatus] = mapped_column(SqlEnum(ProposalStatus, name="proposal_status", values_callable=lambda obj: [member.value for member in obj]), default=ProposalStatus.PENDING, nullable=False, index=True)
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class BrowserBridgeDevice(Base):
    __tablename__ = "browser_bridge_devices"
    __table_args__ = (UniqueConstraint("organization_id", "device_id", name="uq_bridge_device_per_org"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False, index=True)
    pairing_secret_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    access_token_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    granted_capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Only an explicit preferred device may receive the first command of a
    # multi-browser task. Subsequent commands bind to the task's first device.
    is_preferred: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    # Only a minimized active-browser context is retained: hostname, title and
    # observed timestamp. Full URLs, page text, cookies and credentials never
    # leave the extension merely to evaluate a trusted-site policy.
    active_context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    active_context_history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    session_state: Mapped[str] = mapped_column(String(32), default="AUTHORIZING", nullable=False)
    pause_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    authorized_origins: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    authorized_capabilities: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    session_authorized_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    paired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class TaskContext(Base):
    __tablename__ = "task_contexts"

    id: Mapped[uuid.UUID] = uuid_pk()
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("browser_bridge_devices.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    thread_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("task_threads.id", ondelete="SET NULL"), nullable=True, index=True)
    task_goal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    planned_steps: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    completed_steps: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    current_step: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    tab_history: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    active_tab_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    locked_tab_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    session_start_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)


class OperationEvent(Base):
    __tablename__ = "operation_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("browser_bridge_devices.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    thread_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("task_threads.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_data: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("browser_sessions.id", ondelete="SET NULL"), nullable=True)


class BrowserBridgeTrustRule(Base):
    __tablename__ = "browser_bridge_trust_rules"
    __table_args__ = (UniqueConstraint("device_id", "hostname", name="uq_bridge_trust_rule_device_host"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("browser_bridge_devices.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    allowed_capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class BrowserBridgeCommand(Base):
    __tablename__ = "browser_bridge_commands"

    id: Mapped[uuid.UUID] = uuid_pk()
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("browser_bridge_devices.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("task_threads.id", ondelete="SET NULL"), nullable=True, index=True)
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    step_attempt_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("agent_step_attempts.id", ondelete="SET NULL"), nullable=True, index=True)
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("browser_sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    sequence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    command_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False, index=True)
    # Browser permission readiness is deliberately separate from task approval
    # and command execution. Missing site permission never consumes a command.
    preflight_status: Mapped[str] = mapped_column(String(32), default="waiting", nullable=False, index=True)
    preflight_origin: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    preflight_reason: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    preflight_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    # Bounded before/after observations and deterministic verification are
    # stored separately from the raw command result for audit and UI display.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    verification: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    # A command-specific operator lease is separate from the extension claim
    # timeout. It lets Stop/Take over invalidate an in-flight completion even
    # when a stale extension callback arrives later.
    lease_token: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class LibraryCollection(Base):
    __tablename__ = "library_collections"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_library_collection_name"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class LibraryDocument(Base):
    __tablename__ = "library_documents"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    collection_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("library_collections.id", ondelete="SET NULL"), nullable=True, index=True)
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    source_attachment_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), default="upload", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ready", nullable=False, index=True)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class LibraryChunk(Base):
    __tablename__ = "library_chunks"
    __table_args__ = (UniqueConstraint("document_id", "chunk_index", name="uq_library_chunk_index"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("library_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class CommandProject(Base):
    __tablename__ = "command_projects"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class CommandWorkItem(Base):
    __tablename__ = "command_work_items"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("command_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    assignee_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("task_threads.id", ondelete="SET NULL"), nullable=True, index=True)
    proposal_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("action_proposals.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="todo", nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class WorkItemComment(Base):
    __tablename__ = "work_item_comments"

    id: Mapped[uuid.UUID] = uuid_pk()
    work_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("command_work_items.id", ondelete="CASCADE"), nullable=False, index=True)
    author_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    notification_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    href: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class AutomationTrigger(Base):
    __tablename__ = "automation_triggers"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_automation_trigger_name"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), default="webhook", nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    last_received_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class MorningBriefing(Base):
    __tablename__ = "morning_briefings"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", "briefing_date", name="uq_morning_briefing_per_day"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    briefing_date: Mapped[str] = mapped_column(String(10), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    signal_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    proposal_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


__all__ = [
    "ActionProposal",
    "AutomationSubscription",
    "AutomationTrigger",
    "BrowserBridgeCommand",
    "BrowserBridgeDevice",
    "BrowserBridgeTrustRule",
    "BrowserSession",
    "BrowserSessionEvent",
    "CommandProject",
    "CommandWorkItem",
    "ConnectorAuthType",
    "ConnectorConnection",
    "ConnectorStatus",
    "LibraryChunk",
    "LibraryCollection",
    "LibraryDocument",
    "MorningBriefing",
    "Notification",
    "OAuthAccount",
    "OAuthAuthorizationState",
    "ProactiveSignal",
    "ProactiveSignalStatus",
    "ProposalStatus",
    "Organization",
    "OrganizationMember",
    "TaskEvent",
    "OutboxEvent",
    "AgentRun",
    "AgentStepAttempt",
    "AgentEvidence",
    "AgentArtifact",
    "TaskStatus",
    "TaskThread",
    "ToolApproval",
    "User",
    "UserRole",
    "UserSession",
    "WorkItemComment",
]


class CustomConnector(Base):
    """A user-owned remote connector definition.

    ``configuration`` contains non-secret routing metadata only. Authentication
    values and custom MCP headers live in ``encrypted_credentials`` and are never
    returned by the management API after creation.
    """

    __tablename__ = "custom_connectors"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", "slug", name="uq_custom_connector_owner_slug"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)  # api | mcp
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    health_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    usage_guide: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    encrypted_credentials: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="configured", nullable=False, index=True)
    last_tested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ScheduledTaskExecution(Base):
    """One durable attempt to run a user-configured scheduled agent task."""

    __tablename__ = "scheduled_task_executions"

    id: Mapped[uuid.UUID] = uuid_pk()
    schedule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scheduled_agent_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    task_thread_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="claimed", nullable=False, index=True)
    # The exact due instant claimed by this occurrence. It lets recovery and
    # recurring scheduling reason about the requested occurrence rather than
    # drifting from a delayed worker wall clock.
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    # A live worker renews this bounded lease while streaming task events. A
    # subsequent worker may recover only an expired claimed/running execution.
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class MarketingCampaign(Base):
    """A lightweight organization-scoped campaign operating record."""

    __tablename__ = "marketing_campaigns"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="backlog", nullable=False, index=True)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ToolExecutionRecord(Base):
    """Durable record that a non-idempotent tool call has already been made.

    The executor already refuses to auto-retry anything that is not read-only,
    but that only protects the retry loop inside one execution. It does nothing
    about the cases that matter most for a write: the tool succeeded on the far
    side and the response was lost, the process died between the call and the
    commit, or the user re-ran the task and the planner emitted the same step
    again. In each of those the email is sent twice and the invoice is filed
    twice, with no error anywhere to explain it.

    A claim is written before the call and completed after it, so a replay finds
    the claim and replays the recorded result instead of acting again.
    """

    __tablename__ = "tool_execution_records"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_tool_execution_key"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    # The arguments the call was made with. A replay with different arguments
    # under the same key is a bug, not a replay, and is refused rather than
    # silently answered with the first call's result.
    arguments_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    # Deliberately not a foreign key. A safety record that is deleted along with
    # the task protects nothing, and inserting it must not depend on the task
    # row existing -- a record is written before the call, and a task that is
    # removed underneath it must not take the claim with it.
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    step_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # in_progress -> completed | failed. A claim left in_progress belongs to a
    # process that died mid-call, which is exactly the ambiguous case.
    status: Mapped[str] = mapped_column(
        String(24), default="in_progress", nullable=False, index=True
    )
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )