"""Gap 4: core ModelRouter must not silently drop tools when client supports them."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_core_router_passes_tools_when_client_accepts():
    from app.core.model_router import ModelRouter

    received = {}

    class ClientWithTools:
        config = SimpleNamespace(provider="fake")

        async def chat(
            self,
            messages,
            system_prompt=None,
            temperature=None,
            max_tokens=None,
            tools=None,
            tool_choice=None,
            **kwargs,
        ):
            received["tools"] = tools
            received["tool_choice"] = tool_choice
            return SimpleNamespace(
                content="ok",
                model="fake",
                provider="fake",
                usage=None,
                tool_calls=None,
            )

    router = ModelRouter.__new__(ModelRouter)
    router._role_map = {
        "planning": ClientWithTools(),
        "verification": ClientWithTools(),
        "fallback": ClientWithTools(),
    }
    router.ROLE_PLANNING = "planning"
    router.ROLE_ALIASES = {"loop": "planning"}
    # Avoid network fallbacks
    router._deepseek = ClientWithTools()
    router._kimi = ClientWithTools()

    tools = [{"type": "function", "function": {"name": "finish"}}]
    await router.route(
        "loop",
        [{"role": "user", "content": "hi"}],
        tools=tools,
        tool_choice="auto",
    )
    assert received.get("tools") == tools
    assert received.get("tool_choice") == "auto"


@pytest.mark.asyncio
async def test_core_router_fallback_without_tools_on_typeerror():
    """Client without tools in signature must not crash; path does not claim native_tools."""
    from app.core.model_router import ModelRouter

    calls = []

    class ClientNoTools:
        config = SimpleNamespace(provider="legacy")

        async def chat(self, messages, system_prompt=None, temperature=None, max_tokens=None):
            calls.append("no_tools")
            return SimpleNamespace(
                content="ok",
                model="legacy",
                provider="legacy",
                usage=None,
                tool_calls=None,
            )

    router = ModelRouter.__new__(ModelRouter)
    router._role_map = {
        "planning": ClientNoTools(),
        "verification": ClientNoTools(),
        "fallback": ClientNoTools(),
    }
    router.ROLE_PLANNING = "planning"
    router.ROLE_ALIASES = {}
    router._deepseek = ClientNoTools()
    router._kimi = ClientNoTools()

    tools = [{"type": "function", "function": {"name": "finish"}}]
    resp = await router.route(
        "planning",
        [{"role": "user", "content": "hi"}],
        tools=tools,
    )
    assert getattr(resp, "content", None) == "ok"
    assert "no_tools" in calls
