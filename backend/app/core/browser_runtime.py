"""Unified browser-agent runtime contracts.

This module is intentionally pure: it contains no FastAPI, database, or browser
imports. The graph, Bridge service, extension protocol, and tests can therefore
share the same safety vocabulary without duplicating state-machine logic.
"""
from __future__ import annotations

import hashlib
import uuid
from contextvars import ContextVar
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class BrowserSessionStatus(StrEnum):
    AUTHORIZING = "authorizing"
    AUTHORIZED = "authorized"
    RUNNING = "running"
    PAUSED = "paused"
    TAKEOVER = "takeover"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class BrowserFailureClass(StrEnum):
    STALE_TARGET = "stale_target"
    PAGE_LOADING = "page_loading"
    ORIGIN_CHANGED = "origin_changed"
    PERMISSION_REQUIRED = "permission_required"
    AUTHENTICATION_REQUIRED = "authentication_required"
    HUMAN_CHALLENGE = "human_challenge"
    EXTENSION_DISCONNECTED = "extension_disconnected"
    TAB_CLOSED = "tab_closed"
    VERIFICATION_FAILED = "verification_failed"
    UNKNOWN = "unknown"


TERMINAL_SESSION_STATES = frozenset({BrowserSessionStatus.COMPLETED, BrowserSessionStatus.FAILED, BrowserSessionStatus.CANCELLED, BrowserSessionStatus.EXPIRED})
_command_context: ContextVar[dict[str, Any] | None] = ContextVar("browser_command_context", default=None)

def set_command_context(value: dict[str, Any] | None):
    return _command_context.set(value)

def reset_command_context(token: Any) -> None:
    _command_context.reset(token)

def get_command_context() -> dict[str, Any] | None:
    return _command_context.get()
_ALLOWED = {
    BrowserSessionStatus.AUTHORIZING: {BrowserSessionStatus.AUTHORIZED, BrowserSessionStatus.CANCELLED, BrowserSessionStatus.EXPIRED},
    BrowserSessionStatus.AUTHORIZED: {BrowserSessionStatus.RUNNING, BrowserSessionStatus.CANCELLED, BrowserSessionStatus.EXPIRED},
    BrowserSessionStatus.RUNNING: {BrowserSessionStatus.PAUSED, BrowserSessionStatus.TAKEOVER, BrowserSessionStatus.COMPLETED, BrowserSessionStatus.FAILED, BrowserSessionStatus.CANCELLED, BrowserSessionStatus.EXPIRED},
    BrowserSessionStatus.PAUSED: {BrowserSessionStatus.RUNNING, BrowserSessionStatus.TAKEOVER, BrowserSessionStatus.CANCELLED, BrowserSessionStatus.EXPIRED},
    BrowserSessionStatus.TAKEOVER: {BrowserSessionStatus.RUNNING, BrowserSessionStatus.PAUSED, BrowserSessionStatus.CANCELLED, BrowserSessionStatus.EXPIRED},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def page_digest(observation: dict[str, Any]) -> str:
    material = "|".join(str(observation.get(key, "")) for key in ("url", "title", "text", "elements", "media", "scroll_y"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def normalize_origin(value: Any) -> str:
    """Reduce a grant or a target URL to a comparable ``scheme://host[:port]``.

    Grants and targets must normalize identically or an explicitly authorized
    site silently fails its own scope check, so both sides funnel through here.
    A bare host with no scheme is preserved as-is rather than guessed at, which
    keeps a malformed grant from matching a real URL.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        return text.lower().rstrip("/")
    parts = urlsplit(text)
    if not parts.scheme or not parts.netloc:
        return text.lower().rstrip("/")
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def target_origin(action: dict[str, Any]) -> str:
    """Best-effort origin of the site an action is about to touch.

    Reads the URL-bearing tool arguments a bridge action actually carries. This
    is derived from the action rather than from a caller-supplied argument so a
    caller cannot opt out of its own scope check by omitting the parameter.
    """
    arguments = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
    for key in ("url", "target_url", "navigate_url", "origin", "site", "start_url"):
        candidate = arguments.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return normalize_origin(candidate)
    return ""


@dataclass
class BrowserSessionState:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str | None = None
    status: BrowserSessionStatus = BrowserSessionStatus.AUTHORIZING
    mode: str = "task_workspace"
    authorized_origins: set[str] = field(default_factory=set)
    capabilities: set[str] = field(default_factory=set)
    tab_ids: set[int] = field(default_factory=set)
    primary_tab_id: int | None = None
    current_step_id: str | None = None
    observation: dict[str, Any] = field(default_factory=dict)
    last_action: dict[str, Any] = field(default_factory=dict)
    sequence: int = 0

    def transition(self, target: BrowserSessionStatus) -> None:
        if target == self.status:
            return
        if target not in _ALLOWED.get(self.status, set()):
            raise ValueError(f"Invalid browser session transition: {self.status} -> {target}")
        self.status = target
        self.sequence += 1

    def authorize(self, origins: list[str], capabilities: list[str], tab_id: int | None = None) -> None:
        if self.status != BrowserSessionStatus.AUTHORIZING:
            raise ValueError("Only an authorizing session can be authorized.")
        self.authorized_origins = {normalized for normalized in (normalize_origin(origin) for origin in origins) if normalized}
        self.capabilities = {str(capability).strip() for capability in capabilities if str(capability).strip()}
        if tab_id is not None:
            self.primary_tab_id = int(tab_id)
            self.tab_ids.add(int(tab_id))
        self.transition(BrowserSessionStatus.AUTHORIZED)

    def assert_runnable(self, *, origin: str | None = None, capability: str | None = None, tab_id: int | None = None) -> None:
        """Gate an action against this session's granted scope.

        Every check fails CLOSED. An empty grant is an explicit refusal to act,
        not an absence of restriction: the previous ``and self.authorized_origins``
        form silently skipped the check whenever the grant was empty, which
        turned "authorize nothing" into "allow everything".
        """
        if self.status not in {BrowserSessionStatus.AUTHORIZED, BrowserSessionStatus.RUNNING}:
            raise RuntimeError(f"Browser session is {self.status}; the action cannot run.")
        if origin is not None:
            if not self.authorized_origins:
                raise RuntimeError("No browser origins are authorized for this session; the action cannot run.")
            if normalize_origin(origin) not in self.authorized_origins:
                raise RuntimeError("The current browser origin is outside this session's authorization scope.")
        if capability is not None:
            if not self.capabilities:
                raise RuntimeError("No capabilities are granted to this browser session; the action cannot run.")
            if capability not in self.capabilities:
                raise RuntimeError(f"Capability '{capability}' was not granted to this browser session.")
        if tab_id is not None:
            if not self.tab_ids:
                raise RuntimeError("No browser tab is bound to this session; the action cannot run.")
            if tab_id not in self.tab_ids:
                raise RuntimeError("The target tab is not owned by this browser session.")

    def begin_step(self, step_id: str, action: dict[str, Any], *, capability: str | None = None) -> dict[str, Any]:
        # Scope is enforced here, on the action about to run, so that it cannot
        # be skipped by a caller that simply omits the optional arguments.
        self.assert_runnable(
            origin=target_origin(action) or None,
            capability=capability,
            # Focused-tab sessions intentionally follow the tab the user is
            # viewing when the command executes. Task-tab sessions retain the
            # stronger primary-tab invariant.
            tab_id=self.primary_tab_id if self.mode == "task_tab" else None,
        )
        if self.status == BrowserSessionStatus.AUTHORIZED:
            self.transition(BrowserSessionStatus.RUNNING)
        self.current_step_id = str(step_id)
        self.last_action = {"step_id": str(step_id), "action": action, "started_at": now_iso()}
        return {"session_id": self.session_id, "step_id": str(step_id), "sequence": self.sequence, "action": action}

    def record_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(observation)
        normalized["observed_at"] = normalized.get("observed_at") or now_iso()
        normalized["page_digest"] = normalized.get("page_digest") or page_digest(normalized)
        self.observation = normalized
        return normalized

    def verify(self, expected: dict[str, Any], after: dict[str, Any] | None = None) -> dict[str, Any]:
        observed = after if after is not None else self.observation
        checks = []
        for field, wanted in expected.items():
            actual = observed.get(field)
            passed = actual == wanted
            checks.append({"field": field, "expected": wanted, "actual": actual, "passed": passed})
        failed = [check for check in checks if not check["passed"]]
        return {"status": "verified" if not failed else "failed", "checks": checks, "failed_count": len(failed), "verified_at": now_iso()}


def classify_browser_failure(error: Any) -> BrowserFailureClass:
    text = str(error).lower()
    if "stale" in text or "no longer present" in text or "target" in text and "visible" in text:
        return BrowserFailureClass.STALE_TARGET
    if "loading" in text or "not ready" in text:
        return BrowserFailureClass.PAGE_LOADING
    if "origin" in text or "hostname" in text:
        return BrowserFailureClass.ORIGIN_CHANGED
    if "permission" in text:
        return BrowserFailureClass.PERMISSION_REQUIRED
    if "login" in text or "authentication" in text or "sign in" in text:
        return BrowserFailureClass.AUTHENTICATION_REQUIRED
    if "captcha" in text or "mfa" in text or "verification" in text or "human" in text:
        return BrowserFailureClass.HUMAN_CHALLENGE
    if "disconnect" in text or "websocket" in text:
        return BrowserFailureClass.EXTENSION_DISCONNECTED
    if "tab" in text and "closed" in text:
        return BrowserFailureClass.TAB_CLOSED
    if "postcondition" in text or "verify" in text:
        return BrowserFailureClass.VERIFICATION_FAILED
    return BrowserFailureClass.UNKNOWN


def recovery_decision(failure: BrowserFailureClass, *, attempts: int, idempotent: bool) -> dict[str, Any]:
    if failure in {BrowserFailureClass.STALE_TARGET, BrowserFailureClass.PAGE_LOADING, BrowserFailureClass.VERIFICATION_FAILED} and attempts < 2:
        return {"decision": "reobserve_replan", "requires_fresh_observation": True}
    if failure in {BrowserFailureClass.AUTHENTICATION_REQUIRED, BrowserFailureClass.HUMAN_CHALLENGE, BrowserFailureClass.PERMISSION_REQUIRED}:
        return {"decision": "takeover", "requires_user": True}
    if failure in {BrowserFailureClass.ORIGIN_CHANGED, BrowserFailureClass.TAB_CLOSED}:
        return {"decision": "stop", "requires_user": False}
    if failure == BrowserFailureClass.EXTENSION_DISCONNECTED and attempts < 2:
        return {"decision": "reconnect", "requires_fresh_observation": True}
    if idempotent and attempts < 2:
        return {"decision": "retry", "requires_fresh_observation": True}
    return {"decision": "fail", "requires_user": False}


def runtime_from_state(state: dict[str, Any]) -> BrowserSessionState:
    raw = state.get("browser_session") if isinstance(state.get("browser_session"), dict) else {}
    if not raw and state.get("browser_session_id"):
        raw = {
            "session_id": state.get("browser_session_id"),
            "mode": state.get("browser_session_mode", "task_workspace"),
            "status": BrowserSessionStatus.AUTHORIZED.value if state.get("browser_session_authorized") else BrowserSessionStatus.AUTHORIZING.value,
            "authorized_origins": state.get("browser_session_origins") or [],
            "capabilities": state.get("browser_session_capabilities") or [],
            "tab_ids": state.get("browser_session_tab_ids") or [],
            "primary_tab_id": state.get("browser_session_tab_id"),
        }
    status = BrowserSessionStatus(str(raw.get("status", BrowserSessionStatus.AUTHORIZING.value)))
    origins = {normalized for normalized in (normalize_origin(origin) for origin in (raw.get("authorized_origins") or [])) if normalized}
    capabilities = {str(capability).strip() for capability in (raw.get("capabilities") or []) if str(capability).strip()}
    tab_ids = {int(item) for item in (raw.get("tab_ids") or []) if isinstance(item, int) and not isinstance(item, bool)}
    primary_tab_id = raw.get("primary_tab_id")
    if primary_tab_id is not None and not isinstance(primary_tab_id, bool):
        try:
            primary_tab_id = int(primary_tab_id)
        except (TypeError, ValueError):
            primary_tab_id = None
    return BrowserSessionState(
        session_id=str(raw.get("session_id") or uuid.uuid4()), task_id=raw.get("task_id"), status=status,
        mode=str(raw.get("mode") or "task_workspace"), authorized_origins=origins,
        capabilities=capabilities, tab_ids=tab_ids,
        primary_tab_id=primary_tab_id, current_step_id=raw.get("current_step_id"),
        observation=raw.get("observation") if isinstance(raw.get("observation"), dict) else {},
        last_action=raw.get("last_action") if isinstance(raw.get("last_action"), dict) else {}, sequence=int(raw.get("sequence", 0) or 0),
    )


def runtime_to_state(runtime: BrowserSessionState) -> dict[str, Any]:
    return {
        "session_id": runtime.session_id, "task_id": runtime.task_id, "status": runtime.status.value, "mode": runtime.mode,
        "authorized_origins": sorted(runtime.authorized_origins), "capabilities": sorted(runtime.capabilities),
        "tab_ids": sorted(runtime.tab_ids), "primary_tab_id": runtime.primary_tab_id, "current_step_id": runtime.current_step_id,
        "observation": runtime.observation, "last_action": runtime.last_action, "sequence": runtime.sequence,
    }