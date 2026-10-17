"""Uniform model complete() for the agent loop."""
from __future__ import annotations
import json, os
from dataclasses import dataclass, field
from typing import Any
import structlog

logger = structlog.get_logger("onsite48.runtime.models")

MODEL_CALL_HARD_TIMEOUT_S = float(os.environ.get("MODEL_CALL_HARD_TIMEOUT_S", "300"))

# Optional injected router (set by loop context / tests)
_injected_router: Any = None


def set_model_router(router: Any) -> None:
    """Bind a product router for this process (or clear with None)."""
    global _injected_router
    _injected_router = router


def get_injected_router() -> Any:
    return _injected_router


@dataclass
class CompleteResult:
    content: str | None
    tool_calls: list[dict[str, Any]]
    model: str
    provider: str
    path: str
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float | None = 0.0
    raw: Any = None


LANES = ("loop", "worker", "verifier", "summarizer", "vision")

_ROLE_ALIASES = {
    "loop": "planning",
    "worker": "planning",
    "vision": "planning",
    "verifier": "verification",
    "summarizer": "verification",
}


def _resolve_router(explicit: Any = None) -> Any:
    if explicit is not None:
        return explicit
    if _injected_router is not None:
        return _injected_router
    try:
        from app.core import instances
        router = getattr(instances, "model_router", None)
        if router is not None:
            return router
    except Exception:
        pass
    try:
        from app.core import model_router_kimi_deepseek as kd
        getter = getattr(kd, "get_router", None)
        if callable(getter):
            router = getter()
            if router is not None:
                return router
        router = getattr(kd, "router", None)
        if router is not None:
            return router
    except Exception:
        pass
    try:
        from app.core import model_router as mr
        getter = getattr(mr, "get_router", None)
        if callable(getter):
            router = getter()
            if router is not None:
                return router
        router = getattr(mr, "router", None)
        if router is not None:
            return router
    except Exception:
        pass
    try:
        from app.core.model_router_kimi_deepseek import KimiDeepSeekRouter
        logger.debug("runtime.models.using_ephemeral_kimi_deepseek_router")
        return KimiDeepSeekRouter()
    except Exception:
        pass
    try:
        from app.core.model_router import ModelRouter
        logger.debug("runtime.models.using_ephemeral_model_router")
        return ModelRouter()
    except Exception:
        return None


async def complete(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    role: str = "loop",
    tool_choice: str = "auto",
    temperature: float | None = None,
    max_tokens: int | None = None,
    model_override: str | None = None,
    model: str | None = None,
    model_router: Any = None,
) -> CompleteResult:
    import asyncio as _asyncio
    try:
        return await _asyncio.wait_for(
            _complete_impl(
                messages, tools, role=role, tool_choice=tool_choice,
                temperature=temperature, max_tokens=max_tokens,
                model_override=model_override, model=model, model_router=model_router,
            ),
            timeout=MODEL_CALL_HARD_TIMEOUT_S,
        )
    except _asyncio.TimeoutError as exc:
        raise RuntimeError(
            f"Model call exceeded hard timeout of {MODEL_CALL_HARD_TIMEOUT_S:.0f}s"
        ) from exc


async def _complete_impl(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    role: str = "loop",
    tool_choice: str = "auto",
    temperature: float | None = None,
    max_tokens: int | None = None,
    model_override: str | None = None,
    model: str | None = None,
    model_router: Any = None,
) -> CompleteResult:
    tools = tools or []
    # Workstream B: model strategy defaults (explicit temperature/max_tokens win).
    try:
        from app.runtime.model_strategy import TaskSignals, decide_and_record
        _lane = role if role in ("fast", "verifier", "coder", "browser", "researcher", "planner") else None
        signals = TaskSignals(
            explicit_lane=_lane,
            is_verification=role in ("verifier", "verification", "summarizer"),
            is_classification=role in ("fast", "intent", "title"),
        )
        decision = decide_and_record(signals)
        if temperature is None:
            temperature = decision.temperature
        if max_tokens is None:
            max_tokens = decision.max_tokens
        if role in ("loop", "worker"):
            role = decision.router_role or role
        logger.debug(
            "runtime.models.strategy",
            lane=decision.lane,
            profile=decision.profile,
            timeout_s=decision.timeout_s,
        )
    except Exception:
        pass
    # Phase 1: accept model="fast" as an alias for the cheapest/fastest provider (DeepSeek)
    if model == "fast":
        try:
            from app.runtime.lane_defaults import DEEPSEEK_DEFAULT
            model = DEEPSEEK_DEFAULT
        except Exception:
            model = os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat"
        model_override = model
    if model_override:
        model_name = model_override
    else:
        try:
            from app.runtime.lane_defaults import model_for_lane
            model_name = model_for_lane(role if role in LANES else "loop")
        except Exception:
            model_name = os.environ.get("AGENT_LOOP_MODEL") or "default"
    try:
        result = await _complete_via_router(
            messages, tools, role=role, tool_choice=tool_choice,
            temperature=temperature, max_tokens=max_tokens,
            model_override=model_override or model_name,
            model_router=model_router,
        )
        if result is not None:
            return result
    except Exception as exc:
        logger.warning("runtime.models.router_failed", error=str(exc), error_type=type(exc).__name__)
        if _is_infra_error(exc):
            raise

    fallback_messages = list(messages)
    tool_desc = json.dumps(tools, ensure_ascii=False)[:12000]
    fallback_messages.append({
        "role": "user",
        "content": (
            'Respond with a single JSON object only. Schema:\n'
            '{"content": string|null, "tool_calls": [{"id": string, "name": string, "arguments": object}]}\n'
            "Tools:\n" + tool_desc
        ),
    })
    raw_text = await _raw_complete(
        fallback_messages, temperature=temperature, max_tokens=max_tokens or 2048,
        model_router=model_router, role=role,
    )
    parsed = _parse_json_tool_response(raw_text)
    path = "json_fallback"
    if parsed is None:
        repair = fallback_messages + [
            {"role": "assistant", "content": raw_text or ""},
            {"role": "user", "content": "Reply with valid JSON only."},
        ]
        raw_text = await _raw_complete(
            repair, temperature=0.0, max_tokens=max_tokens or 2048,
            model_router=model_router, role=role,
        )
        parsed = _parse_json_tool_response(raw_text)
    if parsed is None:
        return CompleteResult(
            content=raw_text, tool_calls=[], model=model_name,
            provider="unknown", path="json_fallback_failed", cost_usd=None,
        )
    return CompleteResult(
        content=parsed.get("content"),
        tool_calls=parsed.get("tool_calls") or [],
        model=model_name,
        provider="unknown",
        path=path,
        cost_usd=None,
        raw=parsed,
    )


def _is_infra_error(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    markers = ("api key", "auth", "unauthorized", "401", "403", "credentials", "not configured")
    return any(m in msg for m in markers) or "auth" in name


async def _complete_via_router(
    messages, tools, *, role, tool_choice, temperature, max_tokens, model_override, model_router=None,
):
    router = _resolve_router(model_router)
    if router is None:
        return None

    router_role = _ROLE_ALIASES.get(role, role)

    if hasattr(router, "route"):
        try:
            resp = await router.route(
                router_role,
                messages,
                tools=tools or None,
                tool_choice=tool_choice if tools else None,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            path = "native_tools" if tools else "native"
            return _normalize_router_response(resp, path=path)
        except TypeError as exc:
            logger.debug("runtime.models.route_no_tools_kwarg", error=str(exc))
            try:
                resp = await router.route(router_role, messages, temperature=temperature, max_tokens=max_tokens)
                return _normalize_router_response(resp, path="json_fallback" if tools else "native")
            except Exception as exc2:
                logger.warning("runtime.models.route_failed", error=str(exc2))
        except Exception as exc:
            logger.warning("runtime.models.route_failed", error=str(exc))
            if _is_infra_error(exc):
                raise

    for method_name in ("complete", "chat", "route_with_tools", "generate"):
        method = getattr(router, method_name, None)
        if method is None:
            continue
        try:
            resp = await method(
                messages=messages, tools=tools, tool_choice=tool_choice,
                temperature=temperature, max_tokens=max_tokens,
                model=model_override, role=router_role,
            )
            return _normalize_router_response(resp, path="native_tools" if tools else "native")
        except TypeError:
            try:
                resp = await method(role=router_role, messages=messages)
                return _normalize_router_response(resp, path="native" if not tools else "json_fallback")
            except Exception:
                continue
        except Exception as exc:
            logger.debug("runtime.models.method_failed", method=method_name, error=str(exc))
    return None


def _normalize_router_response(resp: Any, *, path: str) -> CompleteResult:
    content = getattr(resp, "content", None) or (resp.get("content") if isinstance(resp, dict) else None)
    model = getattr(resp, "model", None) or (resp.get("model") if isinstance(resp, dict) else "default") or "default"
    provider = getattr(resp, "provider", None) or (resp.get("provider") if isinstance(resp, dict) else "unknown") or "unknown"
    usage_obj = getattr(resp, "usage", None)
    usage: dict[str, int] = {}
    if usage_obj is not None:
        if isinstance(usage_obj, dict):
            usage = {
                "input_tokens": int(usage_obj.get("input_tokens") or usage_obj.get("prompt_tokens") or 0),
                "output_tokens": int(usage_obj.get("output_tokens") or usage_obj.get("completion_tokens") or 0),
            }
        else:
            usage = {
                "input_tokens": int(getattr(usage_obj, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(usage_obj, "output_tokens", 0) or 0),
            }

    cost_usd = getattr(resp, "cost_usd", None)
    if cost_usd is None and isinstance(resp, dict):
        cost_usd = resp.get("cost_usd")
    if cost_usd is not None:
        try:
            cost_usd = float(cost_usd)
        except (TypeError, ValueError):
            cost_usd = None

    tool_calls: list[dict[str, Any]] = []
    raw_calls = getattr(resp, "tool_calls", None) or (resp.get("tool_calls") if isinstance(resp, dict) else None)
    if raw_calls:
        for i, tc in enumerate(raw_calls):
            if isinstance(tc, dict):
                args = tc.get("arguments") or tc.get("args") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"_raw": args}
                name = tc.get("name") or (tc.get("function") or {}).get("name") or ""
                tool_calls.append({
                    "id": tc.get("id") or f"call_{i}",
                    "name": name,
                    "arguments": args if isinstance(args, dict) else {},
                })
            else:
                fn = getattr(tc, "function", None)
                name = getattr(fn, "name", None) if fn else getattr(tc, "name", "")
                args_s = getattr(fn, "arguments", None) if fn else getattr(tc, "arguments", "{}")
                try:
                    args = json.loads(args_s) if isinstance(args_s, str) else (args_s or {})
                except json.JSONDecodeError:
                    args = {"_raw": args_s}
                tool_calls.append({
                    "id": getattr(tc, "id", None) or f"call_{i}",
                    "name": name or "",
                    "arguments": args if isinstance(args, dict) else {},
                })
    if not tool_calls and content:
        parsed = _parse_json_tool_response(content if isinstance(content, str) else None)
        if parsed and parsed.get("tool_calls"):
            tool_calls = parsed["tool_calls"]
            content = parsed.get("content")
            path = "json_fallback"

    content_out: str | None
    if isinstance(content, str) or content is None:
        content_out = content
    else:
        content_out = json.dumps(content)

    return CompleteResult(
        content=content_out,
        tool_calls=tool_calls,
        model=str(model),
        provider=str(provider),
        path=path,
        usage=usage,
        cost_usd=cost_usd if cost_usd is not None else 0.0,
        raw=resp,
    )


async def _raw_complete(
    messages, *, temperature=None, max_tokens=None, model_router=None, role: str = "loop",
) -> str:
    try:
        router = _resolve_router(model_router)
        if router is not None and hasattr(router, "route"):
            router_role = _ROLE_ALIASES.get(role, role)
            resp = await router.route(router_role, messages, temperature=temperature, max_tokens=max_tokens)
            return str(getattr(resp, "content", "") or "")
    except Exception as exc:
        logger.warning("runtime.models.raw_complete_failed", error=str(exc))
    return ""


def _parse_json_tool_response(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
        else:
            return None
    if not isinstance(data, dict):
        return None
    normalized = []
    for i, tc in enumerate(data.get("tool_calls") or []):
        if not isinstance(tc, dict):
            continue
        args = tc.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"_raw": args}
        normalized.append({
            "id": tc.get("id") or f"call_{i}",
            "name": str(tc.get("name") or ""),
            "arguments": args if isinstance(args, dict) else {},
        })
    return {"content": data.get("content"), "tool_calls": normalized}
