"""Mock HTTP proof that Kimi/DeepSeek chat payloads include tools when provided."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_config(provider: str = "kimi", model: str = "kimi-latest") -> Any:
    return SimpleNamespace(
        provider=provider,
        model=model,
        api_key="test-key",
        base_url="https://api.example.test/v1",
        timeout=30.0,
        temperature=0.7,
        max_tokens=1024,
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
    )


def _synthetic_completion_with_tools() -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {
                                "name": "workspace_list",
                                "arguments": '{"path": "."}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "workspace_list",
            "description": "List workspace files",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


@pytest.mark.asyncio
async def test_kimi_chat_payload_includes_tools():
    """Outbound HTTP JSON for KimiK2Client must contain tools when tools= is passed."""
    from app.core.model_router_kimi_deepseek import KimiK2Client

    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return _synthetic_completion_with_tools()

    async def fake_post(url, *, json=None, **kwargs):
        captured["url"] = url
        captured["json"] = json
        return FakeResponse()

    client = KimiK2Client(_make_config("kimi", "kimi-latest"))
    client._client = MagicMock()
    client._client.post = AsyncMock(side_effect=fake_post)

    resp = await client.chat(
        messages=[{"role": "user", "content": "list files"}],
        tools=TOOLS,
        tool_choice="auto",
    )

    assert "json" in captured
    body = captured["json"]
    assert "tools" in body, "Kimi payload must include tools key"
    assert isinstance(body["tools"], list) and len(body["tools"]) > 0
    assert body.get("tool_choice") == "auto"
    assert getattr(resp, "tool_calls", None), "ModelResponse.tool_calls must be non-empty"
    tc = resp.tool_calls[0]
    assert tc.get("name") == "workspace_list" or (
        isinstance(tc.get("function"), dict) and tc["function"].get("name") == "workspace_list"
    )
    args = tc.get("arguments") or (tc.get("function") or {}).get("arguments")
    if isinstance(args, str):
        args = json.loads(args)
    assert isinstance(args, dict)


@pytest.mark.asyncio
async def test_deepseek_chat_payload_includes_tools():
    """Outbound HTTP JSON for DeepSeekClient must contain tools when tools= is passed."""
    from app.core.model_router_kimi_deepseek import DeepSeekClient

    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return _synthetic_completion_with_tools()

    async def fake_post(url, *, json=None, **kwargs):
        captured["url"] = url
        captured["json"] = json
        return FakeResponse()

    client = DeepSeekClient(_make_config("deepseek", "deepseek-chat"))
    client._client = MagicMock()
    client._client.post = AsyncMock(side_effect=fake_post)

    resp = await client.chat(
        messages=[{"role": "user", "content": "list files"}],
        tools=TOOLS,
        tool_choice="auto",
    )

    assert "json" in captured
    body = captured["json"]
    assert "tools" in body, "DeepSeek payload must include tools key"
    assert isinstance(body["tools"], list) and len(body["tools"]) > 0
    assert body.get("tool_choice") == "auto"
    assert getattr(resp, "tool_calls", None), "ModelResponse.tool_calls must be non-empty"
    tc = resp.tool_calls[0]
    args = tc.get("arguments") or (tc.get("function") or {}).get("arguments")
    if isinstance(args, str):
        args = json.loads(args)
    assert isinstance(args, dict)
