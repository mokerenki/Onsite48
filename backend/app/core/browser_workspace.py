"""Task-owned Browser Workspace — Manus-style Browser Operator foundation.

This module defines the canonical domain model that replaces the previous
"focused / selected / active tab" execution identity.

Invariant
---------
A BrowserSession is a durable execution resource *owned by a Task*.
The agent never derives its execution target from the user's currently focused
tab. The task owns a workspace (window + tab group + tabs). The extension
reports browser state; the backend owns task identity and authorization.

Lifecycle (server-authoritative)
--------------------------------
DISCONNECTED → AUTHORIZED → STARTING → ACTIVE
  → PAUSED_FOR_USER | PAUSED_FOR_APPROVAL | TAKEN_OVER
  → RESUMING → ACTIVE
  → COMPLETING → STOPPED | FAILED | EXPIRED

Authorization is always server-side. The extension never supplies authoritative
capability grants.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID


class BrowserSessionStatus(str, Enum):
    DISCONNECTED = "disconnected"
    AUTHORIZING = "authorizing"
    AUTHORIZED = "authorized"
    STARTING = "starting"
    ACTIVE = "active"
    PAUSED_FOR_USER = "paused_for_user"
    PAUSED_FOR_APPROVAL = "paused_for_approval"
    TAKEN_OVER = "taken_over"
    RESUMING = "resuming"
    COMPLETING = "completing"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    EXPIRED = "expired"


TERMINAL_STATUSES = {
    BrowserSessionStatus.STOPPED,
    BrowserSessionStatus.FAILED,
    BrowserSessionStatus.EXPIRED,
}

ACTIVE_EXECUTION_STATUSES = {
    BrowserSessionStatus.ACTIVE,
    BrowserSessionStatus.RESUMING,
}

USER_CONTROL_STATUSES = {
    BrowserSessionStatus.PAUSED_FOR_USER,
    BrowserSessionStatus.PAUSED_FOR_APPROVAL,
    BrowserSessionStatus.TAKEN_OVER,
}


class BrowserCapability(str, Enum):
    READ_PAGE = "READ_PAGE"
    READ_SCREEN = "READ_SCREEN"
    NAVIGATE = "NAVIGATE"
    CLICK = "CLICK"
    TYPE = "TYPE"
    KEYBOARD = "KEYBOARD"
    SCROLL = "SCROLL"
    DRAG = "DRAG"
    UPLOAD = "UPLOAD"
    DOWNLOAD = "DOWNLOAD"
    CLIPBOARD = "CLIPBOARD"
    MEDIA_CONTROL = "MEDIA_CONTROL"
    TAKEOVER = "TAKEOVER"
    CREATE_TAB = "CREATE_TAB"
    CLOSE_TAB = "CLOSE_TAB"
    MANAGE_TAB_GROUP = "MANAGE_TAB_GROUP"


# High-risk capabilities that always require policy review or explicit approval.
HIGH_RISK_CAPABILITIES = {
    BrowserCapability.UPLOAD,
    BrowserCapability.DOWNLOAD,
    BrowserCapability.CLIPBOARD,
}


@dataclass(frozen=True)
class BrowserAuthorization:
    """Server-authoritative grant for a task to use a browser device."""

    authorization_id: UUID
    user_id: UUID
    task_id: UUID
    browser_device_id: UUID
    session_id: UUID
    allowed_origins: tuple[str, ...]
    allowed_capabilities: tuple[str, ...]
    created_at: datetime
    expires_at: datetime
    revoked_at: Optional[datetime] = None
    status: str = "active"

    @property
    def is_valid(self) -> bool:
        if self.revoked_at is not None:
            return False
        if self.status != "active":
            return False
        now = datetime.now(timezone.utc)
        return now < self.expires_at


@dataclass
class TaskTab:
    """One tab that belongs to a task workspace."""

    tab_id: int
    window_id: Optional[int] = None
    url: str = ""
    title: str = ""
    origin: str = ""
    is_primary: bool = False
    created_at: Optional[datetime] = None
    last_observed_at: Optional[datetime] = None
    observation_id: Optional[str] = None


@dataclass
class TaskTabGroup:
    """Chrome tab group (or equivalent) owned by the task."""

    group_id: Optional[int] = None  # Chrome tabGroupId when available
    title: str = ""
    color: str = "blue"
    collapsed: bool = False
    tab_ids: list[int] = field(default_factory=list)


@dataclass
class BrowserWorldState:
    """Normalized multi-layer observation of a single page."""

    session_id: UUID
    window_id: Optional[int]
    tab_id: int
    url: str
    title: str
    origin: str
    viewport: dict[str, Any] = field(default_factory=dict)
    screenshot_ref: Optional[str] = None
    dom_snapshot_ref: Optional[str] = None
    accessibility_snapshot_ref: Optional[str] = None
    interactive_elements: list[dict[str, Any]] = field(default_factory=list)
    dialogs: list[dict[str, Any]] = field(default_factory=list)
    forms: list[dict[str, Any]] = field(default_factory=list)
    scroll: dict[str, Any] = field(default_factory=dict)
    loading_state: str = "unknown"
    navigation_state: str = "unknown"
    timestamp: Optional[datetime] = None
    observation_id: str = ""
    world_revision: int = 0


@dataclass
class BrowserWorkspace:
    """The full task-owned browser workspace."""

    session_id: UUID
    task_id: UUID
    device_id: Optional[UUID]
    status: BrowserSessionStatus
    authorization: Optional[BrowserAuthorization]
    window_id: Optional[int] = None
    tab_group: Optional[TaskTabGroup] = None
    tabs: list[TaskTab] = field(default_factory=list)
    primary_tab_id: Optional[int] = None
    current_world: Optional[BrowserWorldState] = None
    lease_expires_at: Optional[datetime] = None
    stop_reason: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def tab_by_id(self, tab_id: int) -> Optional[TaskTab]:
        for t in self.tabs:
            if t.tab_id == tab_id:
                return t
        return None

    def owns_tab(self, tab_id: int) -> bool:
        return any(t.tab_id == tab_id for t in self.tabs)

    @property
    def is_executable(self) -> bool:
        return self.status in ACTIVE_EXECUTION_STATUSES and (
            self.authorization is None or self.authorization.is_valid
        )


# ---------------------------------------------------------------------------
# Action target — observation-bound, never raw selector as primary identity
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ActionTarget:
    """Target resolved against a specific observation of the world."""

    observation_id: str
    target_id: str
    target_fingerprint: str
    semantic_target: str
    bounding_box: Optional[dict[str, float]] = None
    center: Optional[tuple[float, float]] = None
    source: str = "dom"  # dom | accessibility | visual | coordinate


@dataclass(frozen=True)
class BrowserAction:
    """A proposed or executed computer action against a workspace."""

    action: str  # click, type, navigate, press_key, scroll, ...
    target: Optional[ActionTarget] = None
    value: Optional[str] = None  # text to type, URL, key combo, etc.
    session_id: Optional[UUID] = None
    tab_id: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


DEFAULT_AUTHORIZATION_TTL = timedelta(minutes=30)
DEFAULT_LEASE_TTL = timedelta(minutes=5)

TASK_WORKSPACE_MODE = "task_workspace"  # the only supported mode going forward
LEGACY_FOCUSED_MODE = "focused_tab"  # retained only for migration / rejection


def task_workspace_title(task_name: str) -> str:
    """Human-visible Chrome tab-group title."""
    clean = (task_name or "Task").strip()[:48]
    return f"Onsite48 — {clean}"


def is_high_risk_action(action: str, capability: str | None = None) -> bool:
    high = {
        "upload", "download", "send", "submit_payment", "delete", "publish",
        "share", "transfer", "purchase", "change_password", "change_security",
    }
    if action.lower() in high:
        return True
    if capability and capability in {c.value for c in HIGH_RISK_CAPABILITIES}:
        return True
    return False
