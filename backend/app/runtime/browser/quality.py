"""Phase 5 — Browser observation / action quality helpers.

Improves what the model sees and how the runtime reacts to flaky pages:

* Score observation usefulness (interactive density, title/url, dialogs)
* Compact observations to a bounded element set for the context window
* Detect low-quality observations that warrant a re-observe
* Classify action receipts for retry guidance (stale vs hard fail)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

# How many interactive elements to keep in the model-facing observation.
MAX_ELEMENTS_FOR_MODEL = int(os.environ.get("BROWSER_MAX_ELEMENTS", "40"))
MIN_QUALITY_SCORE = float(os.environ.get("BROWSER_MIN_QUALITY", "0.25"))

# Roles considered interactive for prioritization.
_INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "link",
        "textbox",
        "searchbox",
        "checkbox",
        "radio",
        "combobox",
        "listbox",
        "menuitem",
        "tab",
        "switch",
        "slider",
        "option",
        "file",
    }
)


@dataclass
class QualityReport:
    score: float  # 0.0 – 1.0
    interactive_count: int = 0
    total_elements: int = 0
    has_title: bool = False
    has_url: bool = False
    has_dialog: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def is_low_quality(self) -> bool:
        return self.score < MIN_QUALITY_SCORE

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 3),
            "interactive_count": self.interactive_count,
            "total_elements": self.total_elements,
            "has_title": self.has_title,
            "has_url": self.has_url,
            "has_dialog": self.has_dialog,
            "is_low_quality": self.is_low_quality,
            "reasons": list(self.reasons),
        }


def _element_as_dict(el: Any) -> dict[str, Any]:
    if isinstance(el, dict):
        return el
    if hasattr(el, "to_dict"):
        return el.to_dict()
    return {
        "ref": getattr(el, "ref", ""),
        "role": getattr(el, "role", ""),
        "name": getattr(el, "name", ""),
        "tag": getattr(el, "tag", ""),
        "type": getattr(el, "type", ""),
        "fingerprint": getattr(el, "fingerprint", ""),
        "frame_id": getattr(el, "frame_id", ""),
        "state": getattr(el, "state", None),
        "bbox": getattr(el, "bbox", None),
    }


def _is_interactive(el: dict[str, Any]) -> bool:
    role = str(el.get("role") or "").lower()
    tag = str(el.get("tag") or "").lower()
    typ = str(el.get("type") or "").lower()
    if role in _INTERACTIVE_ROLES:
        return True
    if tag in ("a", "button", "input", "select", "textarea", "option"):
        return True
    if typ in ("submit", "button", "checkbox", "radio", "file", "password"):
        return True
    state = el.get("state") or {}
    if isinstance(state, dict) and (state.get("password") or state.get("file_input")):
        return True
    return False


def score_observation(obs: Any) -> QualityReport:
    """Score how useful an observation is for acting."""
    if obs is None:
        return QualityReport(score=0.0, reasons=["null_observation"])

    if hasattr(obs, "to_dict"):
        data = obs.to_dict()
    elif isinstance(obs, dict):
        data = obs
    else:
        return QualityReport(score=0.0, reasons=["unknown_type"])

    elements = [_element_as_dict(e) for e in (data.get("elements") or [])]
    interactive = [e for e in elements if _is_interactive(e)]
    title = str(data.get("title") or "").strip()
    url = str(data.get("url") or "").strip()
    has_dialog = bool(data.get("dialog"))

    reasons: list[str] = []
    score = 0.0

    if url and url.startswith("http"):
        score += 0.2
    else:
        reasons.append("missing_or_invalid_url")

    if title:
        score += 0.15
    else:
        reasons.append("missing_title")

    n_el = len(elements)
    n_int = len(interactive)
    if n_int >= 5:
        score += 0.4
    elif n_int >= 2:
        score += 0.25
    elif n_int == 1:
        score += 0.1
        reasons.append("few_interactive_elements")
    else:
        reasons.append("no_interactive_elements")

    if n_el >= 10:
        score += 0.15
    elif n_el >= 3:
        score += 0.08

    if has_dialog:
        score += 0.1
        reasons.append("dialog_present")

    # blank / about:blank pages
    if url in ("about:blank", "", "chrome://newtab/"):
        score = min(score, 0.15)
        reasons.append("blank_page")

    score = max(0.0, min(1.0, score))
    return QualityReport(
        score=score,
        interactive_count=n_int,
        total_elements=n_el,
        has_title=bool(title),
        has_url=bool(url and url.startswith("http")),
        has_dialog=has_dialog,
        reasons=reasons,
    )


def compact_observation(
    obs: Any,
    *,
    max_elements: int = MAX_ELEMENTS_FOR_MODEL,
) -> dict[str, Any]:
    """Return a model-sized observation: interactive elements first, capped."""
    if hasattr(obs, "to_dict"):
        data = obs.to_dict()
    elif isinstance(obs, dict):
        data = dict(obs)
    else:
        return {"error": "invalid_observation"}

    elements = [_element_as_dict(e) for e in (data.get("elements") or [])]
    interactive = [e for e in elements if _is_interactive(e)]
    passive = [e for e in elements if not _is_interactive(e)]

    # Prefer named interactive controls; then unnamed; then passive with names.
    def _rank(e: dict[str, Any]) -> tuple:
        name = str(e.get("name") or "").strip()
        return (0 if name else 1, -len(name))

    interactive.sort(key=_rank)
    passive.sort(key=_rank)
    selected = (interactive + passive)[: max(1, int(max_elements))]

    quality = score_observation(data)
    out = {
        "observation_id": data.get("observation_id"),
        "revision": data.get("revision"),
        "url": data.get("url"),
        "title": data.get("title"),
        "frames": data.get("frames") or [],
        "elements": selected,
        "element_count_total": len(elements),
        "element_count_returned": len(selected),
        "dialog": data.get("dialog"),
        "scroll": data.get("scroll"),
        "quality": quality.to_dict(),
    }
    if data.get("screenshot_ref"):
        out["screenshot_ref"] = data["screenshot_ref"]
    return out


def classify_receipt(receipt: Any) -> dict[str, Any]:
    """Classify an action receipt for retry guidance."""
    if hasattr(receipt, "to_dict"):
        data = receipt.to_dict()
    elif isinstance(receipt, dict):
        data = receipt
    else:
        return {"kind": "unknown", "should_retry": False, "should_reobserve": False}

    stale = bool(data.get("stale_observation"))
    ok = data.get("ok")
    if ok is None:
        # receipts often use success/error_code
        ok = not data.get("error_code") and not stale

    policy = str(data.get("policy") or data.get("decision") or "").upper()
    error = str(data.get("error_code") or data.get("error") or "")

    if stale:
        return {
            "kind": "stale",
            "should_retry": True,
            "should_reobserve": True,
            "guidance": "Re-observe, then retry the action with the new observation_id and element_ref.",
        }
    if policy in ("TAKEOVER", "APPROVAL", "DENY"):
        return {
            "kind": "policy",
            "should_retry": False,
            "should_reobserve": False,
            "guidance": f"Policy {policy}: do not bypass; follow the reason on the receipt.",
            "policy": policy,
        }
    if not ok or error:
        return {
            "kind": "error",
            "should_retry": error in ("timeout", "element_not_visible", "not_found"),
            "should_reobserve": True,
            "guidance": "Action failed; re-observe before retrying.",
            "error_code": error or None,
        }
    return {
        "kind": "ok",
        "should_retry": False,
        "should_reobserve": False,
        "guidance": "Action succeeded; continue from the post-action observation.",
    }


def enrich_observe_result(obs: Any) -> dict[str, Any]:
    """Build the tool result for an observe action with quality + compact view."""
    compact = compact_observation(obs)
    quality = compact.get("quality") or score_observation(obs).to_dict()
    return {
        "ok": True,
        "action": "observe",
        "observation": compact,
        "quality": quality,
        "reobserve_recommended": bool(quality.get("is_low_quality")),
    }


def enrich_action_result(receipt: Any) -> dict[str, Any]:
    """Attach quality classification to an action receipt dict."""
    if hasattr(receipt, "to_dict"):
        data = receipt.to_dict()
    elif isinstance(receipt, dict):
        data = dict(receipt)
    else:
        data = {"raw": str(receipt)}
    data["quality"] = classify_receipt(data)
    # Compact nested observation if present
    if isinstance(data.get("observation"), dict) and data["observation"].get("elements"):
        data["observation"] = compact_observation(data["observation"])
    return data


__all__ = [
    "QualityReport",
    "score_observation",
    "compact_observation",
    "classify_receipt",
    "enrich_observe_result",
    "enrich_action_result",
    "MAX_ELEMENTS_FOR_MODEL",
    "MIN_QUALITY_SCORE",
]
