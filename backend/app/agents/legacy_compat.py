"""Minimal symbols retained after Phase 3 cutover for API/websocket layers.

The planner/executor/verifier topology was deleted. These helpers are pure
functions with no dependency on the old brain.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SAFE_TASK_SCOPED_TOOLS = {
    "bridge_read_active_tab",
    "bridge_inspect_active_tab",
    "bridge_scroll_active_tab",
    "bridge_list_tabs",
}

SCHEDULE_HARD_STOP_TOOLS = frozenset({
    "gmail_send_message",
    "gmail_delete_draft",
    "gmail_delete_message",
    "gmail_trash_message",
    "slack_send_message",
    "github_create_issue",
    "atlassian_create_issue",
})


def approval_fingerprint(
    *,
    organization_id: str = "",
    tool_name: str = "",
    tool_args: dict[str, Any] | None = None,
    policy_version: str = "v1",
    thread_id: str = "",
) -> str:
    payload = {
        "org": organization_id,
        "tool": tool_name,
        "args": tool_args or {},
        "policy": policy_version,
        "thread": thread_id,
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def confirmation_key(
    thread_id: str,
    step_index: int,
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    organization_id: str = "",
    policy_version: str = "v1",
) -> str:
    """Action identity independent of plan step position."""
    return approval_fingerprint(
        organization_id=organization_id,
        tool_name=tool_name,
        tool_args=tool_args,
        policy_version=policy_version,
        thread_id=thread_id,
    )
