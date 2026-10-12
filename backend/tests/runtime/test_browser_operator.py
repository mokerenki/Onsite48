"""Unit tests for protocol-v4 BrowserOperator (no live network)."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.runtime.browser.operator import BrowserOperator
from app.runtime.browser.protocol import ActionRequest, ActionTarget


class FakePage:
    """Minimal async page stub for operator unit tests."""

    def __init__(self, html: str = "<html><body><button>Delete Account</button><input type='password' name='pw'/><a href='/'>Home</a></body></html>"):
        self.url = "https://example.com/app"
        self._html = html
        self.viewport_size = {"width": 1280, "height": 720}
        self.keyboard = MagicMock()
        self.keyboard.press = AsyncMock()
        self.keyboard.type = AsyncMock()
        self.mouse = MagicMock()
        self.mouse.click = AsyncMock()
        self.mouse.wheel = AsyncMock()
        self.main_frame = self
        self.frames = [self]
        self.name = "main"

    async def title(self):
        return "Example App"

    async def evaluate(self, script, *args):
        # Return canned elements for the accessibility scrape script
        if "querySelectorAll" in str(script) or "maxN" in str(script):
            return [
                {
                    "tag": "button",
                    "role": "button",
                    "name": "Delete Account",
                    "type": "",
                    "disabled": False,
                    "checked": False,
                    "readonly": False,
                    "required": False,
                    "value": "",
                    "password": False,
                    "file_input": False,
                    "bbox": {"x": 10, "y": 10, "w": 120, "h": 32},
                    "path": "body>button",
                },
                {
                    "tag": "input",
                    "role": "textbox",
                    "name": "pw",
                    "type": "password",
                    "disabled": False,
                    "checked": False,
                    "readonly": False,
                    "required": True,
                    "value": "",
                    "password": True,
                    "file_input": False,
                    "bbox": {"x": 10, "y": 50, "w": 200, "h": 28},
                    "path": "body>input",
                },
                {
                    "tag": "a",
                    "role": "link",
                    "name": "Home",
                    "type": "",
                    "disabled": False,
                    "checked": False,
                    "readonly": False,
                    "required": False,
                    "value": "",
                    "password": False,
                    "file_input": False,
                    "bbox": {"x": 10, "y": 90, "w": 40, "h": 20},
                    "path": "body>a",
                },
            ]
        if "scrollX" in str(script):
            return {"x": 0, "y": 0, "max_x": 0, "max_y": 500}
        return None

    async def inner_text(self, sel):
        return "Example App content"

    async def screenshot(self, **kwargs):
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

    async def goto(self, url, **kwargs):
        self.url = url

    def locator(self, sel):
        loc = MagicMock()
        loc.first = loc
        loc.click = AsyncMock()
        loc.select_option = AsyncMock()
        loc.set_input_files = AsyncMock()
        loc.nth = MagicMock(return_value=loc)
        return loc


@pytest.mark.asyncio
async def test_observe_returns_protocol_shape():
    op = BrowserOperator(FakePage())
    obs = await op.observe()
    d = obs.to_dict()
    assert d["observation_id"].startswith("obs_")
    assert d["revision"] == 1
    assert d["url"] == "https://example.com/app"
    assert len(d["elements"]) >= 2
    el = d["elements"][0]
    assert "ref" in el and "fingerprint" in el and "frame_id" in el
    assert el["bbox"]["w"] > 0


@pytest.mark.asyncio
async def test_stale_observation_on_bad_ref():
    op = BrowserOperator(FakePage())
    obs = await op.observe()
    req = ActionRequest(
        action="click",
        target=ActionTarget(observation_id=obs.observation_id, element_ref="e9999"),
    )
    receipt = await op.act(req)
    assert receipt.stale_observation is True
    assert receipt.ok is False
    assert receipt.observation is not None


@pytest.mark.asyncio
async def test_stale_on_observation_id_mismatch():
    op = BrowserOperator(FakePage())
    await op.observe()
    req = ActionRequest(
        action="click",
        target=ActionTarget(observation_id="obs_old_wrong", element_ref="e1"),
    )
    receipt = await op.act(req)
    assert receipt.stale_observation is True


@pytest.mark.asyncio
async def test_password_field_takeover():
    op = BrowserOperator(FakePage())
    obs = await op.observe()
    pw = next(e for e in obs.elements if e.state.password)
    req = ActionRequest(
        action="type",
        target=ActionTarget(observation_id=obs.observation_id, element_ref=pw.ref),
        text="secret",
    )
    receipt = await op.act(req)
    assert receipt.ok is False
    assert receipt.policy is not None
    assert receipt.policy["decision"] == "TAKEOVER"


@pytest.mark.asyncio
async def test_destructive_button_approval():
    op = BrowserOperator(FakePage())
    obs = await op.observe()
    btn = next(e for e in obs.elements if "Delete" in e.name)
    req = ActionRequest(
        action="click",
        target=ActionTarget(observation_id=obs.observation_id, element_ref=btn.ref),
    )
    receipt = await op.act(req)
    assert receipt.ok is False
    assert receipt.policy is not None
    assert receipt.policy["decision"] == "APPROVAL"


@pytest.mark.asyncio
async def test_safe_click_allowed():
    op = BrowserOperator(FakePage())
    obs = await op.observe()
    link = next(e for e in obs.elements if e.role == "link")
    req = ActionRequest(
        action="click",
        target=ActionTarget(observation_id=obs.observation_id, element_ref=link.ref),
    )
    receipt = await op.act(req)
    assert receipt.ok is True
    assert receipt.observation is not None
    assert receipt.diff is not None
