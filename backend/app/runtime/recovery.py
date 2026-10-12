"""Deterministic tool-failure classification and recovery (Phase 3).

No LLM calls. Every tool error is classified; the loop applies the corresponding
policy (backoff, refresh, schema feedback, permanent stop).
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class FailureClass(str, Enum):
    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    AUTH_EXPIRED = "auth_expired"
    INVALID_ARGUMENTS = "invalid_arguments"
    PRECONDITION_FAILED = "precondition_failed"
    PERMANENT = "permanent"
    LOOP_DETECTED = "loop_detected"


@dataclass
class RecoveryAction:
    failure_class: FailureClass
    retryable: bool
    backoff_s: float = 0.0
    retry_after_s: float | None = None
    suggestion: str = ""
    take_over: bool = False  # surface to user / ask_user
    max_retries: int = 0


# Patterns are structural signals in error payloads, not intent keywords on goals.
_RATE_LIMIT_RE = re.compile(r"rate.?limit|429|quota.?exceed|too many requests|retry.?after", re.I)
_AUTH_RE = re.compile(r"auth|token.?expir|401|403|unauthorized|forbidden|invalid.?credential|refresh.?token", re.I)
_INVALID_ARGS_RE = re.compile(r"invalid.?arg|bad.?arg|schema|type.?error|missing.?required|validation|unknown.?parameter", re.I)
_PRECOND_RE = re.compile(r"precondition|not.?found|404|does not exist|no such|conflict|409|already exists|state mismatch", re.I)
_TRANSIENT_RE = re.compile(r"timeout|timed.?out|connection.?reset|connection.?refused|temporarily|503|502|504|unavailable|network", re.I)

# A word such as "auth" in an arbitrary browser or tool exception is not an
# authorization signal for the task runner. In particular, a page can contain
# an authentication error while the Agent Computer itself has no connector
# credential to refresh. Only explicit codes or structured connector failures
# can enter the connector-refresh branch below.
_AUTH_ERROR_CODES = frozenset({
    "auth_missing",
    "auth_expired",
    "auth_refresh_failed",
    "unauthorized",
    "invalid_credentials",
    "invalid_token",
})
_CONNECTOR_TOOL_PREFIXES = (
    "github_",
    "gmail_",
    "google_calendar_",
    "slack_",
    "outlook_",
    "salesforce_",
    "atlassian_",
    "brand24_",
    "elevenlabs_",
    "similarweb_",
    "custom_connector_",
)


def _is_connector_tool(tool_name: str) -> bool:
    """Return whether a legacy unstructured failure can belong to a connector.

    New connector failures include ``connector_id`` and do not rely on this
    heuristic. The narrow fallback keeps older connector tools recoverable
    without ever treating ``computer_navigate`` or local workspace tools as an
    OAuth integration.
    """
    return str(tool_name or "").lower().startswith(_CONNECTOR_TOOL_PREFIXES)


def classify_failure(
    result: dict[str, Any] | Any,
    *,
    tool_name: str = "",
    same_call_count: int = 0,
    no_progress_count: int = 0,
    same_call_limit: int = 3,
    no_progress_limit: int = 5,
) -> RecoveryAction:
    """Classify a tool result (or exception payload) into a RecoveryAction."""
    if same_call_count >= same_call_limit or no_progress_count >= no_progress_limit:
        return RecoveryAction(
            failure_class=FailureClass.LOOP_DETECTED,
            retryable=False,
            suggestion="Repeated identical tool calls or no progress; stop and report.",
            take_over=True,
        )

    if not isinstance(result, dict):
        text = str(result)
        code = ""
    else:
        code = str(result.get("error_code") or result.get("code") or "").lower()
        text = str(result.get("message") or result.get("error") or result.get("detail") or "")
        # Explicit flags from tools
        if result.get("retryable") is False and code in ("permanent", "not_executable", "unknown_tool"):
            return RecoveryAction(
                failure_class=FailureClass.PERMANENT,
                retryable=False,
                suggestion=text or "Permanent tool failure",
            )
        if result.get("error_code") in ("bad_arguments", "invalid_arguments"):
            return RecoveryAction(
                failure_class=FailureClass.INVALID_ARGUMENTS,
                retryable=True,
                suggestion=text or "Fix argument types/names and retry",
                max_retries=2,
            )
        if result.get("error_code") == "timeout":
            return RecoveryAction(
                failure_class=FailureClass.TRANSIENT,
                retryable=True,
                backoff_s=_backoff(same_call_count),
                suggestion="Timeout; retry with backoff",
                max_retries=3,
            )

    combined = f"{code} {text}"

    if _RATE_LIMIT_RE.search(combined):
        retry_after = _extract_retry_after(result if isinstance(result, dict) else {})
        return RecoveryAction(
            failure_class=FailureClass.RATE_LIMITED,
            retryable=True,
            retry_after_s=retry_after,
            backoff_s=retry_after or _backoff(same_call_count, base=5.0),
            suggestion="Rate limited; honor Retry-After",
            max_retries=4,
        )

    structured_connector_failure = isinstance(result, dict) and bool(result.get("connector_id"))
    explicit_auth_code = code in _AUTH_ERROR_CODES and (
        not tool_name or structured_connector_failure or _is_connector_tool(tool_name)
    )
    legacy_connector_auth = (
        not code
        and _is_connector_tool(tool_name)
        and bool(_AUTH_RE.search(combined))
    )
    if explicit_auth_code or (structured_connector_failure and _AUTH_RE.search(combined)) or legacy_connector_auth:
        reconnect_required = bool(result.get("reconnect_required")) if isinstance(result, dict) else False
        return RecoveryAction(
            failure_class=FailureClass.AUTH_EXPIRED,
            retryable=not reconnect_required,
            suggestion=(
                "Reconnect the affected account in Settings."
                if reconnect_required
                else "Refresh the identified connector once, then retry; otherwise ask the user to reconnect."
            ),
            max_retries=1,
            take_over=reconnect_required,
        )

    if _INVALID_ARGS_RE.search(combined):
        return RecoveryAction(
            failure_class=FailureClass.INVALID_ARGUMENTS,
            retryable=True,
            suggestion=text or "Invalid arguments; correct schema and retry",
            max_retries=2,
        )

    if _PRECOND_RE.search(combined):
        return RecoveryAction(
            failure_class=FailureClass.PRECONDITION_FAILED,
            retryable=True,
            suggestion=text or "Precondition failed; inspect state then retry",
            max_retries=2,
        )

    if _TRANSIENT_RE.search(combined) or (isinstance(result, dict) and result.get("retryable") is True):
        return RecoveryAction(
            failure_class=FailureClass.TRANSIENT,
            retryable=True,
            backoff_s=_backoff(same_call_count),
            suggestion="Transient error; backoff and retry",
            max_retries=3,
        )

    # Default: permanent if error_code present, else treat as transient once
    if isinstance(result, dict) and (result.get("error_code") or result.get("error")):
        return RecoveryAction(
            failure_class=FailureClass.PERMANENT,
            retryable=False,
            suggestion=text or "Unclassified permanent failure",
        )

    return RecoveryAction(
        failure_class=FailureClass.TRANSIENT,
        retryable=True,
        backoff_s=_backoff(same_call_count),
        max_retries=1,
        suggestion="Unclassified; single retry",
    )


def _backoff(attempt: int, base: float = 1.0, cap: float = 30.0) -> float:
    """Exponential backoff with jitter."""
    raw = min(cap, base * (2 ** max(0, attempt)))
    jitter = random.uniform(0, raw * 0.25)
    return round(raw + jitter, 3)


def _extract_retry_after(result: dict) -> float | None:
    for key in ("retry_after", "retry-after", "Retry-After"):
        val = result.get(key)
        if val is None and isinstance(result.get("headers"), dict):
            val = result["headers"].get(key) or result["headers"].get(key.lower())
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return None


async def maybe_refresh_auth(
    tool_name: str,
    org_id: str | None = None,
    *,
    connector_id: str | None = None,
) -> dict[str, Any]:
    """Refresh one identified connector using the authoritative credential path.

    ``connector_vault`` is deliberately a small encryption helper, not a global
    runtime object. The previous code imported a nonexistent singleton and
    converted unrelated tool failures into a bogus "vault unavailable" auth
    message. Provider tools already own the DB-backed refresh flow; this hook
    calls that same flow and never guesses an account from a browser tool.

    ``org_id`` remains accepted for call-site compatibility. The refresh uses
    the current execution identity, which includes the scoped organization and
    user required for the credential lookup.
    """
    del org_id
    try:
        from app.tools.connector_tools import (
            connector_id_for_tool,
            refresh_connector_credentials,
        )
    except Exception:
        return {"refreshed": False, "detail": "connector refresh runtime is unavailable"}

    resolved_connector_id = str(connector_id or connector_id_for_tool(tool_name) or "").strip()
    if not resolved_connector_id:
        return {"refreshed": False, "detail": "no connector was identified for this tool"}

    try:
        await refresh_connector_credentials(resolved_connector_id)
        return {
            "refreshed": True,
            "detail": "credentials refreshed",
            "connector_id": resolved_connector_id,
        }
    except Exception as exc:
        # Connector provider errors have already redacted their detail. Do not
        # copy arbitrary exception text into the task stream or journal.
        reconnect_required = bool(getattr(exc, "reconnect_required", False))
        code = str(getattr(exc, "code", "refresh_failed"))
        return {
            "refreshed": False,
            "detail": "reconnect required" if reconnect_required else "credential refresh failed",
            "connector_id": resolved_connector_id,
            "code": code,
            "reconnect_required": reconnect_required,
        }


def recovery_message(action: RecoveryAction) -> dict[str, Any]:
    """Structured payload the loop can append so the model sees the classification."""
    return {
        "recovery": {
            "class": action.failure_class.value,
            "retryable": action.retryable,
            "backoff_s": action.backoff_s,
            "retry_after_s": action.retry_after_s,
            "suggestion": action.suggestion,
            "take_over": action.take_over,
            "max_retries": action.max_retries,
        }
    }
