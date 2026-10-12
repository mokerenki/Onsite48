"""Browser Protocol v4 — observation + action types shared by sandbox and extension.

This module is the source of truth for the Python side. The JSON Schema at
backend/protocol/browser_v4.schema.json must stay in sync (drift test).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional
import hashlib
import json
import time
import uuid


ActionName = Literal[
    "click", "type", "press_key", "hotkey", "scroll", "wait_for",
    "select", "drag", "upload", "navigate", "open_tab", "close_tab", "observe",
]

PolicyDecisionName = Literal["ALLOW", "APPROVAL", "TAKEOVER", "DENY"]


@dataclass
class BBox:
    x: float
    y: float
    w: float
    h: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ElementState:
    disabled: bool = False
    checked: bool = False
    expanded: bool = False
    selected: bool = False
    focused: bool = False
    value: str = ""
    readonly: bool = False
    required: bool = False
    password: bool = False
    file_input: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Element:
    ref: str
    role: str
    name: str
    state: ElementState
    bbox: BBox
    frame_id: str
    fingerprint: str
    tag: str = ""
    type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "role": self.role,
            "name": self.name,
            "state": self.state.to_dict(),
            "bbox": self.bbox.to_dict(),
            "frame_id": self.frame_id,
            "fingerprint": self.fingerprint,
            "tag": self.tag,
            "type": self.type,
        }


@dataclass
class FrameInfo:
    frame_id: str
    url: str
    name: str
    parent_frame_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Observation:
    observation_id: str
    revision: int
    url: str
    title: str
    frames: list[FrameInfo]
    elements: list[Element]
    screenshot_ref: Optional[str] = None
    set_of_marks: bool = False
    dialog: Optional[dict[str, Any]] = None
    scroll: Optional[dict[str, float]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "revision": self.revision,
            "url": self.url,
            "title": self.title,
            "frames": [f.to_dict() for f in self.frames],
            "elements": [e.to_dict() for e in self.elements],
            "screenshot_ref": self.screenshot_ref,
            "set_of_marks": self.set_of_marks,
            "dialog": self.dialog,
            "scroll": self.scroll,
        }

    def element_by_ref(self, ref: str) -> Optional[Element]:
        for el in self.elements:
            if el.ref == ref:
                return el
        return None


@dataclass
class ActionTarget:
    observation_id: Optional[str] = None
    element_ref: Optional[str] = None
    coordinates: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ActionRequest:
    action: ActionName
    target: Optional[ActionTarget] = None
    text: Optional[str] = None
    key: Optional[str] = None
    keys: Optional[list[str]] = None
    direction: Optional[str] = None
    amount: Optional[float] = None
    url: Optional[str] = None
    file_path: Optional[str] = None
    option: Optional[str] = None
    timeout_ms: Optional[int] = None
    selector_hint: Optional[str] = None
    clear: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"action": self.action}
        if self.target:
            d["target"] = self.target.to_dict()
        for k in ("text", "key", "keys", "direction", "amount", "url", "file_path", "option", "timeout_ms", "selector_hint"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        if self.clear:
            d["clear"] = True
        return d


@dataclass
class ActionReceipt:
    ok: bool
    action: str
    stale_observation: bool = False
    error: Optional[str] = None
    policy: Optional[dict[str, str]] = None
    observation: Optional[Observation] = None
    diff: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ok": self.ok,
            "action": self.action,
            "stale_observation": self.stale_observation,
            "error": self.error,
            "policy": self.policy,
            "diff": self.diff,
        }
        if self.observation:
            d["observation"] = self.observation.to_dict()
        return d


def fingerprint_element(role: str, name: str, tag: str, path: str, frame_id: str) -> str:
    raw = f"{role}|{name}|{tag}|{path}|{frame_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def new_observation_id() -> str:
    return f"obs_{uuid.uuid4().hex[:12]}"


# Keys the drift test expects the backend operator to expose.
PROTOCOL_ACTION_NAMES = (
    "click", "type", "press_key", "hotkey", "scroll", "wait_for",
    "select", "drag", "upload", "navigate", "open_tab", "close_tab", "observe",
)

PROTOCOL_VERSION = "4.0.0"
