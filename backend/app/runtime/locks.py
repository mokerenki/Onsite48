"""Resource locks for concurrent workers.

Keyed by ToolSpec.resources(args) (e.g. gmail:draft:<id>, calendar:event:<id>,
browser:workspace:<id>:tab:<id>). Concurrent mutation of the same resource
waits or returns structured resource_busy. Locks expire with the lease so a
dead worker cannot hold one forever.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

try:
    import structlog
except ImportError:
    structlog = None  # type: ignore


class _NullLogger:
    def debug(self,*a,**k): pass
    def info(self,*a,**k): pass
    def warning(self,*a,**k): pass
    def exception(self,*a,**k): pass

def _get_logger(name):
    if structlog:
        return structlog.get_logger(name)
    return _NullLogger()

logger = _get_logger("onsite48.runtime.locks") if structlog else None

DEFAULT_LOCK_TTL_S = 120.0
DEFAULT_WAIT_S = 15.0


@dataclass
class LockEntry:
    key: str
    holder_id: str
    run_id: str
    acquired_at: float
    expires_at: float
    meta: dict[str, Any] = field(default_factory=dict)


class ResourceBusy(Exception):
    """Raised when a resource is locked and wait timed out."""

    def __init__(self, key: str, holder_id: str, retryable: bool = True):
        self.key = key
        self.holder_id = holder_id
        self.retryable = retryable
        super().__init__(f"resource_busy: {key} held by {holder_id}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": "resource_busy",
            "message": str(self),
            "resource": self.key,
            "holder_id": self.holder_id,
            "retryable": self.retryable,
            "suggestion": "Retry after a short backoff or serialize access to this resource.",
        }


class ResourceLockManager:
    """In-process + optional Redis-backed lock manager.

    Primary path is asyncio locks for same-process fan-out (wide_map workers).
    Lease expiry ensures dead holders release automatically.
    """

    def __init__(self, *, default_ttl_s: float = DEFAULT_LOCK_TTL_S):
        self._default_ttl = default_ttl_s
        self._entries: dict[str, LockEntry] = {}
        self._condition = asyncio.Condition()
        self._local = asyncio.Lock()

    def _purge_expired(self, now: float | None = None) -> None:
        now = now or time.monotonic()
        expired = [k for k, e in self._entries.items() if e.expires_at <= now]
        for k in expired:
            del self._entries[k]

    async def acquire(
        self,
        key: str,
        *,
        holder_id: str | None = None,
        run_id: str = "",
        ttl_s: float | None = None,
        wait_s: float = DEFAULT_WAIT_S,
        meta: dict[str, Any] | None = None,
    ) -> str:
        """Acquire exclusive lock on key. Returns holder_id.

        Waits up to wait_s. On timeout raises ResourceBusy.
        """
        holder_id = holder_id or str(uuid.uuid4())
        ttl = ttl_s if ttl_s is not None else self._default_ttl
        deadline = time.monotonic() + max(0.0, wait_s)

        async with self._condition:
            while True:
                now = time.monotonic()
                self._purge_expired(now)
                entry = self._entries.get(key)
                if entry is None or entry.holder_id == holder_id:
                    self._entries[key] = LockEntry(
                        key=key,
                        holder_id=holder_id,
                        run_id=run_id,
                        acquired_at=now,
                        expires_at=now + ttl,
                        meta=meta or {},
                    )
                    logger.debug("lock_acquired", key=key, holder_id=holder_id, run_id=run_id)
                    return holder_id
                remaining = deadline - now
                if remaining <= 0:
                    raise ResourceBusy(key, entry.holder_id)
                try:
                    await asyncio.wait_for(
                        self._condition.wait(),
                        timeout=min(remaining, 1.0),
                    )
                except asyncio.TimeoutError:
                    continue

    async def release(self, key: str, holder_id: str) -> bool:
        async with self._condition:
            entry = self._entries.get(key)
            if entry is None:
                return False
            if entry.holder_id != holder_id:
                logger.warning(
                    "lock_release_mismatch",
                    key=key,
                    expected=entry.holder_id,
                    got=holder_id,
                )
                return False
            del self._entries[key]
            self._condition.notify_all()
            logger.debug("lock_released", key=key, holder_id=holder_id)
            return True

    async def refresh(self, key: str, holder_id: str, ttl_s: float | None = None) -> bool:
        async with self._condition:
            entry = self._entries.get(key)
            if entry is None or entry.holder_id != holder_id:
                return False
            ttl = ttl_s if ttl_s is not None else self._default_ttl
            entry.expires_at = time.monotonic() + ttl
            return True

    def held_by(self, key: str) -> str | None:
        self._purge_expired()
        e = self._entries.get(key)
        return e.holder_id if e else None

    def snapshot(self) -> list[dict[str, Any]]:
        self._purge_expired()
        return [
            {
                "key": e.key,
                "holder_id": e.holder_id,
                "run_id": e.run_id,
                "expires_in_s": max(0.0, e.expires_at - time.monotonic()),
            }
            for e in self._entries.values()
        ]


# Process-wide default manager (workers in same process share it)
_default_manager: ResourceLockManager | None = None


def get_lock_manager() -> ResourceLockManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = ResourceLockManager()
    return _default_manager


def resources_for_tool(name: str, args: dict[str, Any]) -> list[str]:
    """Derive lock keys from tool name + args.

    Convention: <domain>:<resource_type>:<id>
    Read-only tools return empty list (no lock needed).
    """
    n = (name or "").lower()
    keys: list[str] = []

    if n in ("email_send", "gmail_send", "send_email"):
        draft = args.get("draft_id") or args.get("id")
        if draft:
            keys.append(f"gmail:draft:{draft}")
        else:
            keys.append("gmail:send")
    elif n in ("email_draft", "gmail_draft", "create_draft"):
        draft = args.get("draft_id") or args.get("id") or "new"
        keys.append(f"gmail:draft:{draft}")
    elif n in ("calendar_create", "calendar_update", "calendar_delete"):
        eid = args.get("event_id") or args.get("id") or "new"
        keys.append(f"calendar:event:{eid}")
    elif n in ("browser", "browser_action", "browser_observe", "browser_act", "browser_takeover", "browser_stop"):
        ws = args.get("workspace_id") or args.get("session_id") or "default"
        tab = args.get("tab_id") or args.get("target_id") or "active"
        # Only lock mutating browser actions
        action = (args.get("action") or args.get("kind") or "").lower()
        if action in ("click", "type", "fill", "select", "navigate", "press", "upload"):
            keys.append(f"browser:workspace:{ws}:tab:{tab}")
    elif n in ("file_write", "file_edit"):
        path = args.get("path") or args.get("file") or ""
        if path:
            keys.append(f"file:{path}")
    elif n in ("shell_exec",) and args.get("mutating"):
        keys.append(f"shell:session:{args.get('session_id', 'default')}")

    return keys
