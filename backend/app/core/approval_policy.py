"""Canonical approval identity and risk classification."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def normalize_arguments(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): normalize_arguments(item) for key, item in sorted(value.items()) if key not in {"_browser_session", "_trace_id"}}
    if isinstance(value, (list, tuple)):
        return [normalize_arguments(item) for item in value]
    return value


def approval_fingerprint(
    *,
    organization_id: str,
    thread_id: str,
    tool_name: str,
    tool_args: dict[str, Any],
    policy_version: str = "v1",
) -> str:
    payload = {
        "organization_id": str(organization_id or ""),
        "thread_id": str(thread_id or ""),
        "tool_name": str(tool_name or ""),
        "tool_args": normalize_arguments(tool_args),
        "policy_version": str(policy_version or "v1"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def action_risk(tool_name: str, tool_args: dict[str, Any] | None = None) -> dict[str, Any]:
    name = str(tool_name or "")
    irreversible = any(token in name.lower() for token in ("send", "delete", "trash", "remove", "publish", "submit"))
    external = irreversible or any(token in name.lower() for token in ("create", "update", "upload", "write"))
    return {
        "category": "irreversible" if irreversible else "external_write" if external else "read_only",
        "reversible": not irreversible,
        "requires_approval": external,
        "target": next((str((tool_args or {}).get(key)) for key in ("to", "url", "recipient", "path", "subject") if (tool_args or {}).get(key)), "connected service"),
    }


__all__ = ["action_risk", "approval_fingerprint", "normalize_arguments"]