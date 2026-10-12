"""Pure policy helpers for Onsite48 Bridge active-tab plans."""

from __future__ import annotations

import json
import re
from typing import Any


BRIDGE_SURFACE_OBSERVATION = re.compile(
    r"\b(?:facebook|instagram|linkedin|x|twitter)?\s*(?:notifications?|notification\s+panel|alerts?)\b.*\b(?:what|which|read|show|check|tell|latest|recent|new)\b"
    r"|\b(?:what|which|read|show|check|tell)\b.{0,80}\b(?:notifications?|notification\s+panel|alerts?)\b",
    re.IGNORECASE,
)


BRIDGE_ACTIVE_TAB_SUMMARY = re.compile(
    r"\b(?:active|current|selected)\s+(?:browser\s+)?tab\b.*\b(?:read|get|summari[sz]e|summary|explain|what|title|inspect|report)\b"
    r"|\b(?:read|get|summari[sz]e|summary|explain|what|title|inspect|report)\b.*\b(?:active|current|selected)\s+(?:browser\s+)?tab\b"
    r"|\bsynth\s*bridge\b.*\b(?:active[_\s-]?tab|current[_\s-]?tab|selected[_\s-]?tab|browser\s+tab|page|gmail|mail)\b"
    r"|\b(?:active[_\s-]?tab|current[_\s-]?tab|selected[_\s-]?tab)\s*\.?\s*(?:title|read|summary|inspect|report)\b"
    r"|\b(?:page|tab)\s+i\s+(?:currently\s+)?have\s+open\b"
    r"|\b(?:summari[sz]e|read|what(?:'s|\s+is)\s+on)\s+(?:the\s+)?(?:page|tab)\s+i\s+(?:currently\s+)?have\s+open\b"
    r"|\b(?:paired|my\s+paired|local)\s+(?:browser\s+)?tab\b"
    r"|\buse\s+(?:the\s+)?synth\s*bridge\b"
    r"|\b(?:already\s+open|existing|open)\s+(?:(?:gmail|mail\.google\.com|spotify|linkedin|facebook|instagram)\s+)?(?:browser\s+)?tab\b"
    r"|\b(?:gmail|mail\.google\.com|spotify|linkedin|facebook|instagram)\b.{0,100}\b(?:open|existing|active|current|selected)\s+(?:browser\s+)?tab\b"
    r"|\b(?:gmail|mail\.google\.com|spotify|linkedin|facebook|instagram)\s+(?:browser\s+)?tab\b.{0,100}\b(?:already\s+)?open\b"
    r"|\b(?:local\s+browser|my\s+browser)\b.{0,100}\b(?:tab|gmail|mail\.google\.com|spotify|youtube(?:\s+music)?|music\.youtube\.com|linkedin|facebook|instagram)\b"
    r"|\b(?:youtube\s+music|music\.youtube\.com|youtube|spotify)\b.{0,100}\b(?:tab|currently\s+playing|now\s+playing|current\s+(?:song|track)|track|artist|album|playback)\b"
    r"|\b(?:currently\s+playing|now\s+playing|current\s+(?:song|track))\b.{0,100}\b(?:open|existing|selected|active|local\s+browser)\b",
    re.IGNORECASE,
)


_BRIDGE_NAVIGATE_PATTERN = re.compile(
    r"\b(?:navigate|go\s+to|open|browse\s+to|take\s+(?:the\s+)?tab\s+to)\s+(?:the\s+)?(?:current\s+)?(?:active\s+)?(?:browser\s+)?tab\s+(?:to\s+)?(?P<url>https?://[^\s<>\]\[\)\}\"']+)\b",
    re.IGNORECASE,
)


_BRIDGE_CLICK_PATTERN = re.compile(
    r"\b(?:click|tap|press|pause|play|resume)\s+(?:on\s+)?(?:the\s+)?(?P<target>.+)$",
    re.IGNORECASE,
)


_BRIDGE_MEDIA_PATTERN = re.compile(
    r"\b(?P<action>pause|stop|play|resume|toggle)\b.{0,80}\b(?:youtube(?:\s+music)?|video|audio|media|playback|song|track)\b"
    r"|\b(?:youtube(?:\s+music)?|video|audio|media|playback|song|track)\b.{0,80}\b(?P<reverse>pause|stop|play|resume|toggle)\b",
    re.IGNORECASE,
)


_BRIDGE_FILL_PATTERN = re.compile(
    r"\b(?:type|enter|fill|search)\s+(?P<text>.+?)\s+(?:in|into|to|for)\s+(?:the\s+)?(?P<target>.+?)(?:\s+field|\s+box|\s+input|\s+search|\s+bar)?\s*$",
    re.IGNORECASE,
)

_BRIDGE_SOCIAL_DRAFT_PATTERN = re.compile(
    r"\b(?:instagram|facebook|messenger|linkedin)\b.{0,100}\b(?:chat|conversation|dm|direct|message)s?\b.{0,100}\b(?:draft|write|compose|type)\b"
    r"|\b(?:draft|write|compose|type)\b.{0,100}\b(?:instagram|facebook|messenger|linkedin)\b.{0,100}\b(?:chat|conversation|dm|direct|message)s?\b",
    re.IGNORECASE,
)


def social_chat_draft_plan(goal: str) -> list[dict[str, Any]] | None:
    """Prepare, but never send, a draft in a user-selected social chat tab."""
    if not _BRIDGE_SOCIAL_DRAFT_PATTERN.search(goal or ""):
        return None
    match = re.search(
        r"\b(?:saying|that\s+says|with\s+the\s+message|message)\s*[:\-]?\s*(.+)$",
        goal or "",
        re.IGNORECASE,
    )
    body = (match.group(1) if match else "").strip().strip("\"'")
    if not body:
        return [{
            "step_id": "clarify-social-draft-content",
            "description": "Ask what text should be placed in the selected social-chat composer; do not send anything.",
            "tool_name": None,
            "tool_args": {"question": "What should the draft message say? I will place it in the chat composer but will not send it."},
            "expected_outcome": "The user provides draft text before any local-browser text entry.",
            "on_failure": "pause",
            "idempotency": "read_only",
        }]
    return [
        {
            "step_id": "open-social-messages",
            "description": "Open the visible Messages or Chats area in the user-selected social site tab.",
            "tool_name": "bridge_click_active_tab",
            "tool_args": {"selector": "Messages"},
            "expected_outcome": "The social messaging area is open without sending or submitting anything.",
            "on_failure": "pause",
            "idempotency": "write",
        },
        {
            "step_id": "fill-social-chat-draft",
            "description": "Place the approved draft text into the visible chat composer and leave it unsent.",
            "tool_name": "bridge_fill_active_tab",
            "tool_args": {"selector": "message input", "value": body[:4000]},
            "expected_outcome": "The draft text is present in the chat composer and no send or submit control was activated.",
            "on_failure": "pause",
            "idempotency": "write",
        },
    ]


def _normalize_click_target(goal: str) -> str | None:
    match = _BRIDGE_CLICK_PATTERN.search(goal or "")
    if not match:
        return None
    target = (match.group("target") or "").strip()
    target = re.sub(r"\s+", " ", target).strip()
    target = re.sub(r"\b(?:the|a|an|this|that|it|on|in|to|for|with|by|from|please|now)\b", "", target, flags=re.IGNORECASE).strip()
    return target or None


def _normalize_fill_target(goal: str) -> tuple[str | None, str | None]:
    match = _BRIDGE_FILL_PATTERN.search(goal or "")
    if not match:
        return None, None
    text = (match.group("text") or "").strip()
    target = (match.group("target") or "").strip()
    target = re.sub(r"\s+", " ", target).strip()
    target = re.sub(r"\b(the|a|an|this|that|it|on|in|to|for|with|by|from)\b", "", target, flags=re.IGNORECASE).strip()
    return target or "text input", text


def control_media_plan(goal: str) -> list[dict[str, Any]] | None:
    """Use direct HTML5 media control instead of brittle visual clicking."""
    match = _BRIDGE_MEDIA_PATTERN.search(goal or "")
    if not match:
        return None
    action = (match.group("action") or match.group("reverse") or "").lower()
    action = {"stop": "pause", "resume": "play"}.get(action, action)
    outcome = {"pause": "paused", "play": "playing", "toggle": "toggled"}[action]
    return [{
        "step_id": "bridge-control-media",
        "description": f"{action.title()} the visible media player in the focused local-browser tab using the browser media API.",
        "tool_name": "bridge_control_media",
        "tool_args": {"action": action},
        "expected_outcome": f"The visible HTML5 media player is {outcome} and playback evidence is returned.",
        "idempotency": "idempotent_write",
    }]


def active_tab_summary_plan(goal: str) -> list[dict[str, Any]] | None:
    """Return a safe read-only plan for an active tab or a rendered browser surface."""
    if BRIDGE_SURFACE_OBSERVATION.search(goal or ""):
        return [{
            "step_id": "bridge-observe-surface",
            "description": "Read the currently rendered notification or other requested browser surface from the paired tab.",
            "tool_name": "bridge_observe_surface",
            "tool_args": {"surface": "notifications", "max_items": 20, "max_scrolls": 2},
            "expected_outcome": "A bounded structured list of currently rendered surface entries with page evidence; never infer content that is not rendered.",
        }]
    if not BRIDGE_ACTIVE_TAB_SUMMARY.search(goal or ""):
        return None
    return [{
        "step_id": "bridge-read-active-tab",
        "description": "Read the user-selected browser tab and prepare the requested summary.",
        "tool_name": "bridge_read_active_tab",
        "tool_args": {},
        "expected_outcome": "A structured browser observation containing page content, metadata, semantic elements, and accessibility fallback evidence from the user's explicitly selected local browser tab.",
    }]


def navigate_active_tab_plan(goal: str) -> list[dict[str, Any]] | None:
    """Return a plan to navigate the active selected tab to a public URL."""
    match = _BRIDGE_NAVIGATE_PATTERN.search(goal or "")
    if not match:
        return None
    url = match.group("url")
    return [{
        "step_id": "bridge-navigate-active-tab",
        "description": f"Navigate the selected local-browser tab to {url}.",
        "tool_name": "bridge_navigate_active_tab",
        "tool_args": {"url": url},
        "expected_outcome": f"The selected tab is now at {url}.",
    }]


def locate_and_click_active_tab_plan(goal: str) -> list[dict[str, Any]] | None:
    """Return a two-step plan to locate and click a visible target in the active tab."""
    target = _normalize_click_target(goal)
    if not target:
        return None
    locate_step = {
        "step_id": "bridge-locate-target",
        "description": f"Locate the '{target}' in the selected local-browser tab using visual grounding.",
        "tool_name": "bridge_locate",
        "tool_args": {"description": target},
        "expected_outcome": "The screen coordinates of the requested target are returned for the next click step.",
    }
    click_step = {
        "step_id": "bridge-click-located-target",
        "description": f"Click the located '{target}' using the coordinates from the previous step.",
        "tool_name": "bridge_click_active_tab",
        "tool_args": {"selector": target},
        "expected_outcome": f"The '{target}' was clicked in the selected tab.",
    }
    return [locate_step, click_step]


def locate_and_type_active_tab_plan(goal: str) -> list[dict[str, Any]] | None:
    """Return a two-step plan to locate and type text into a field in the active tab."""
    target, text = _normalize_fill_target(goal)
    if not target or not text:
        return None
    locate_step = {
        "step_id": "bridge-locate-input",
        "description": f"Locate the '{target}' in the selected local-browser tab using visual grounding.",
        "tool_name": "bridge_locate",
        "tool_args": {"description": target},
        "expected_outcome": "The screen coordinates of the requested input field are returned for the next type step.",
    }
    type_step = {
        "step_id": "bridge-type-into-located-input",
        "description": f"Type the approved text into the located '{target}' using the coordinates from the previous step.",
        "tool_name": "bridge_fill_active_tab",
        "tool_args": {"selector": target, "value": text},
        "expected_outcome": f"The text was entered into the '{target}' in the selected tab.",
    }
    return [locate_step, type_step]


def is_active_tab_summary_plan(plan: Any) -> bool:
    """Only a plan containing the constrained read-active-tab tool is exempt."""
    tool_names = {
        str(step.get("tool_name"))
        for step in (plan or [])
        if isinstance(step, dict) and step.get("tool_name")
    }
    return tool_names in ({"bridge_read_active_tab"}, {"bridge_observe_surface"})


def tool_result_output(result: Any) -> str:
    """Convert a tool result to meaningful executor context.

    Standard tools return ``{"output": ...}``. Onsite48 Bridge returns its
    local-browser payload as ``{"command_id": ..., "result": {...}}``.
    Preserving that nested result prevents an approved tab read from becoming
    an empty executor step and triggering unnecessary replanning.
    """
    if not isinstance(result, dict):
        return str(result)
    output = result.get("output")
    if output is not None:
        return str(output)
    nested_result = result.get("result")
    if nested_result is not None:
        if isinstance(nested_result, str):
            return nested_result
        return json.dumps(nested_result, ensure_ascii=False, indent=2, default=str)
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)