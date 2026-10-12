"""Immutable identity and evidence helpers for graph execution.

The graph still uses JSON-compatible dictionaries for compatibility, but every
plan revision, step attempt, observation, receipt, and verification receives a
stable identity. Presentation projections must be derived from evidence and
must never mutate the evidence record.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from typing import Any, Mapping


def new_id() -> str:
    return str(uuid.uuid4())


def new_plan_revision_id() -> str:
    return new_id()


def ensure_revision(state: dict[str, Any], *, force_new: bool = False) -> str:
    current = str(state.get("plan_revision_id") or "")
    if force_new or not current:
        current = new_plan_revision_id()
        state["plan_revision_id"] = current
        state["verified_step_ids"] = []
        state["verified_step_indices"] = []
        prior_tool_data = state.get("tool_data")
        state["tool_data"] = {
            "last": {},
            "historical": copy.deepcopy(prior_tool_data) if isinstance(prior_tool_data, dict) else {},
        }
    return current


def stamp_plan(plan: list[dict[str, Any]], revision_id: str) -> list[dict[str, Any]]:
    stamped: list[dict[str, Any]] = []
    for index, step in enumerate(plan):
        item = copy.deepcopy(step)
        item["plan_revision_id"] = revision_id
        item["step_id"] = str(item.get("step_id") or f"step_{index + 1}")
        item.setdefault("resource_key", resource_key(item.get("tool_name"), item.get("tool_args") or {}))
        item.setdefault("concurrency_safe", False)
        stamped.append(item)
    return stamped


def resource_key(tool_name: Any, args: Mapping[str, Any] | None = None) -> str:
    name = str(tool_name or "synthesis")
    args = args or {}
    explicit = args.get("resource_key") or args.get("account_id") or args.get("session_id")
    if explicit:
        return f"{name}:{str(explicit)[:180]}"
    lower = name.lower()
    if lower.startswith(("gmail_", "google_calendar_", "slack_", "github_", "atlassian_", "custom_api", "custom_mcp")):
        provider = lower.split("_", 1)[0]
        return f"connector:{provider}"
    if lower.startswith(("bridge_", "computer_")):
        return "browser-session"
    return f"tool:{name}"


def active_results(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    revision = str(state.get("plan_revision_id") or "")
    results = state.get("results") or []
    if not revision:
        return [r for r in results if isinstance(r, dict)]
    return [
        r for r in results
        if isinstance(r, dict) and str(r.get("plan_revision_id") or revision) == revision
    ]


def stamp_attempt(result: Mapping[str, Any], *, revision_id: str, step_id: str, step_index: int) -> dict[str, Any]:
    item = copy.deepcopy(dict(result))
    item.setdefault("plan_revision_id", revision_id)
    item.setdefault("step_id", step_id)
    item.setdefault("step_index", step_index)
    item.setdefault("attempt_id", new_id())
    item.setdefault("observation_id", new_id() if item.get("observation") is not None else None)
    item.setdefault("evidence_id", new_id())
    item.setdefault("receipt_id", new_id() if item.get("tool_data") or item.get("output") else None)
    return item


def immutable_evidence(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deep-copied structured evidence envelope."""
    source = result.get("tool_data") if isinstance(result.get("tool_data"), dict) else result.get("output")
    return {
        "evidence_id": str(result.get("evidence_id") or new_id()),
        "attempt_id": str(result.get("attempt_id") or ""),
        "plan_revision_id": str(result.get("plan_revision_id") or ""),
        "step_id": str(result.get("step_id") or ""),
        "source": copy.deepcopy(source),
        "observation": copy.deepcopy(result.get("observation")),
        "receipt": copy.deepcopy(result.get("receipt") or result.get("tool_data")),
        "captured_at": result.get("captured_at"),
    }


def projection(evidence: Mapping[str, Any], *, max_chars: int = 4000) -> dict[str, Any]:
    """Create a bounded model/UI projection without modifying evidence."""
    source = evidence.get("source")
    text = json.dumps(source, ensure_ascii=False, default=str) if isinstance(source, (dict, list)) else str(source or "")
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return {
        "evidence_id": evidence.get("evidence_id"),
        "attempt_id": evidence.get("attempt_id"),
        "step_id": evidence.get("step_id"),
        "summary": text,
        "has_observation": evidence.get("observation") is not None,
        "has_receipt": evidence.get("receipt") is not None,
    }


def evidence_digest(evidence: Mapping[str, Any]) -> str:
    payload = json.dumps(evidence, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_path(value: Any, results: list[Mapping[str, Any]], *, revision_id: str | None = None) -> Any:
    """Resolve $steps.<step_id>.output.<path> against one plan revision only."""
    if not isinstance(value, str) or not value.startswith("$steps."):
        return value
    parts = value.split(".")
    if len(parts) < 4 or parts[2] != "output":
        return None
    step_id = parts[1]
    candidates = [
        item for item in results
        if isinstance(item, Mapping)
        and str(item.get("step_id") or "") == step_id
        and (revision_id is None or str(item.get("plan_revision_id") or "") == revision_id)
    ]
    if not candidates:
        return None
    data = candidates[-1].get("tool_data")
    if not isinstance(data, (dict, list)):
        data = candidates[-1].get("output")
    for segment in parts[3:]:
        match = re.fullmatch(r"([^\[\]]+)(?:\[(\d+)\])?", segment)
        key = match.group(1) if match else segment
        index = int(match.group(2)) if match and match.group(2) is not None else None
        if isinstance(data, Mapping):
            data = data.get(key)
        elif isinstance(data, list) and key.isdigit():
            list_index = int(key)
            data = data[list_index] if 0 <= list_index < len(data) else None
        else:
            return None
        if index is not None:
            data = data[index] if isinstance(data, list) and 0 <= index < len(data) else None
        if data is None:
            return None
    return data