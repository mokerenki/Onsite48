"""Protocol drift test: backend operator surface must match browser_v4.schema.json."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "protocol" / "browser_v4.schema.json"


def test_schema_exists_and_valid():
    assert SCHEMA_PATH.is_file(), f"missing schema at {SCHEMA_PATH}"
    data = json.loads(SCHEMA_PATH.read_text())
    assert data["title"] == "Onsite48 Browser Protocol v4"
    defs = data["definitions"]
    for key in ("Observation", "Element", "Frame", "ActionRequest", "ActionReceipt", "BBox"):
        assert key in defs, f"schema missing definition {key}"


def test_action_enum_matches_python():
    data = json.loads(SCHEMA_PATH.read_text())
    schema_actions = set(data["definitions"]["ActionRequest"]["properties"]["action"]["enum"])
    from app.runtime.browser.protocol import PROTOCOL_ACTION_NAMES
    py_actions = set(PROTOCOL_ACTION_NAMES)
    assert schema_actions == py_actions, f"drift: schema={schema_actions} py={py_actions}"


def test_observation_required_fields():
    data = json.loads(SCHEMA_PATH.read_text())
    required = set(data["definitions"]["Observation"]["required"])
    assert required == {"observation_id", "revision", "url", "title", "frames", "elements"}


def test_element_required_fields():
    data = json.loads(SCHEMA_PATH.read_text())
    required = set(data["definitions"]["Element"]["required"])
    assert "ref" in required and "fingerprint" in required and "frame_id" in required


def test_python_dataclasses_roundtrip():
    from app.runtime.browser.protocol import (
        BBox, Element, ElementState, FrameInfo, Observation, new_observation_id, fingerprint_element,
    )
    el = Element(
        ref="e1",
        role="button",
        name="Submit",
        state=ElementState(),
        bbox=BBox(0, 0, 10, 10),
        frame_id="f0",
        fingerprint=fingerprint_element("button", "Submit", "button", "button", "f0"),
    )
    obs = Observation(
        observation_id=new_observation_id(),
        revision=1,
        url="https://example.com",
        title="Example",
        frames=[FrameInfo("f0", "https://example.com", "main")],
        elements=[el],
    )
    d = obs.to_dict()
    assert d["observation_id"].startswith("obs_")
    assert d["elements"][0]["fingerprint"]
    assert d["elements"][0]["ref"] == "e1"


def test_extension_mentions_capture_unavailable():
    """Extension background.js must implement the Phase-4 capture scope fix."""
    ext = Path(__file__).resolve().parents[3] / "extension" / "onsite48-bridge" / "background.js"
    text = ext.read_text()
    assert "captureTabScoped" in text
    assert "capture_unavailable" in text
    assert "workspace_window" in text or "verified.windowId" in text
