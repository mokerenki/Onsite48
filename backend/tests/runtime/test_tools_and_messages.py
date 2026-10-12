"""Phase A unit tests: message normalizer + JSON tool parser (no API keys)."""
from __future__ import annotations
import json
import pytest

from app.core.model_router_kimi_deepseek import BaseModelClient, ModelResponse, TokenUsage
from app.runtime.models import _parse_json_tool_response, _normalize_router_response, CompleteResult


class _DummyConfig:
    api_key = None
    base_url = "http://example"
    timeout = 5
    model = "test"
    temperature = 0
    max_tokens = 100
    input_cost_per_1m = 1.0
    output_cost_per_1m = 2.0
    provider = "test"


class _Client(BaseModelClient):
    def __init__(self):
        # skip httpx client
        self.config = _DummyConfig()

    async def chat(self, *a, **k):
        raise NotImplementedError


def test_normalize_preserves_tool_turns():
    c = _Client()
    seq = [
        {"role": "user", "content": "list files"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": '{"path": "."}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_abc", "content": '["a.py"]'},
        {"role": "assistant", "content": "Found a.py"},
    ]
    out = [c._normalize_message(m) for m in seq]
    assert out[0]["role"] == "user" and out[0]["content"] == "list files"
    assert out[1]["content"] is None
    assert out[1]["tool_calls"][0]["id"] == "call_abc"
    assert out[1]["tool_calls"][0]["function"]["name"] == "list_dir"
    assert "None" not in str(out[1]["content"])
    assert out[2]["tool_call_id"] == "call_abc"
    assert out[2]["content"] == '["a.py"]'
    assert out[3]["content"] == "Found a.py"


def test_parse_tool_calls_from_message():
    c = _Client()
    msg = {
        "content": None,
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "search", "arguments": '{"q": "x"}'},
            }
        ],
    }
    parsed = c._parse_tool_calls(msg)
    assert parsed and parsed[0]["name"] == "search"
    assert parsed[0]["arguments"] == {"q": "x"}


def test_json_parser_variants():
    valid = _parse_json_tool_response(
        '{"content": null, "tool_calls": [{"id": "1", "name": "t", "arguments": {"a": 1}}]}'
    )
    assert valid and valid["tool_calls"][0]["name"] == "t"

    wrapped = _parse_json_tool_response(
        'Here is JSON:\n```json\n{"content": "hi", "tool_calls": []}\n```'
    )
    assert wrapped and wrapped["content"] == "hi"

    invalid = _parse_json_tool_response("not json at all")
    assert invalid is None

    empty = _parse_json_tool_response('{"content": "ok", "tool_calls": []}')
    assert empty and empty["tool_calls"] == []


def test_cost_copied_from_model_response():
    resp = ModelResponse(
        content="ok",
        model="m",
        provider="kimi",
        usage=TokenUsage(10, 20),
        latency_ms=1.0,
        cost_usd=0.02,
        tool_calls=None,
    )
    cr = _normalize_router_response(resp, path="native_tools")
    assert isinstance(cr, CompleteResult)
    assert cr.cost_usd == 0.02
    assert cr.usage["input_tokens"] == 10
