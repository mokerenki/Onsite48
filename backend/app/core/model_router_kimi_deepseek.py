"""
ModelRouter — Kimi (planner) + NVIDIA/DeepSeek (verification/fallback/vision)

Routes planning to Kimi by default, verification and fallback to NVIDIA/DeepSeek.
All calls traced via Opik with cost attribution.

Kimi model: kimi-latest (OpenAI-compatible endpoint)
Base URL:   https://api.moonshot.ai/v1   (default from config)
NVIDIA:     moonshotai/kimi-k3 (vision-capable)
DeepSeek:   unchanged
"""

import asyncio
import dataclasses
import inspect
import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
import structlog
from opik import track

from app.core.config import CONFIG, ModelConfig
from app.core import tracing
from app.core.model_lanes import LANE_DESCRIPTORS, ModelLane, lane_catalog
from app.core.platform_metrics import metrics

logger = structlog.get_logger("aether.model_router")


def _structured_output_payload(response_schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build the provider request field that constrains output to a schema.

    Every provider here speaks the OpenAI chat-completions dialect, so
    ``json_object`` is the portable floor. Strict ``json_schema`` decoding is
    used when the caller supplies a schema and the deployment opts in, because it
    is not universally available. Callers degrade to lenient JSON parsing when a
    provider rejects the field, so this never turns a plan into a hard failure.
    """
    if not response_schema:
        return {}
    if _truthy(os.getenv("SYNTHAI_STRICT_JSON_SCHEMA"), default=False):
        return {
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": str(response_schema.get("name") or "agent_schema"),
                    "schema": response_schema.get("schema") or response_schema,
                    "strict": True,
                },
            }
        }
    return {"response_format": {"type": "json_object"}}

def _truthy(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _trace_content_enabled() -> bool:
    """Whether model output text may be attached to a trace span.

    Off unless SYNTHAI_TRACE_CONTENT is explicitly truthy. Traces are exported
    off-process, so turning this on moves personal data to whatever backs the
    export; it should be a deliberate, documented per-environment choice.
    """
    return _truthy(os.getenv("SYNTHAI_TRACE_CONTENT"), default=False)


# ------------------------------------------------------------------
# Pricing constants (USD per 1M tokens) – update if needed
# These are for the kimi-k2.6 model via Moonshot official API.
# ------------------------------------------------------------------
KIMI_K26_INPUT_PRICE  = 0.95
KIMI_K26_OUTPUT_PRICE = 4.00
DEEPSEEK_INPUT_PRICE  = 0.14   # V4-Flash
DEEPSEEK_OUTPUT_PRICE = 0.28

# ------------------------------------------------------------------
@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    def total_cost(self, in_cost: float, out_cost: float) -> float:
        return (self.input_tokens / 1_000_000) * in_cost + (self.output_tokens / 1_000_000) * out_cost

@dataclass(frozen=True)
class ModelResponse:
    content: Optional[str]
    model: str
    provider: str
    usage: TokenUsage
    latency_ms: float
    # Computed only from the actual client configuration used for this call.
    # None means pricing is not verified, not that the call was free.
    cost_usd: Optional[float] = None
    raw: Optional[Dict[str, Any]] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None

class BaseModelClient(ABC):
    def __init__(self, config: ModelConfig):
        self.config = config
        headers = {}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        self._client = httpx.AsyncClient(
            base_url=config.base_url or "",
            timeout=httpx.Timeout(config.timeout),
            headers=headers,
        )

    @abstractmethod
    async def chat(
        self,
        messages: List[Any],
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
    ) -> ModelResponse:
        pass

    def _with_structured_output(
        self,
        payload: Dict[str, Any],
        response_schema: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Attach schema constraints, retrying once without them if rejected.

        A provider that does not support ``response_format`` answers 400. That is
        a capability gap, not a task failure, so the request is replayed without
        the constraint and the caller falls back to lenient JSON extraction.
        """
        if not response_schema:
            return payload
        constrained = dict(payload)
        constrained.update(_structured_output_payload(response_schema))
        return constrained

    def _normalize_message(self, msg: Any) -> Dict[str, Any]:
        """Serialize a message for OpenAI-compatible chat completions.

        Preserves assistant tool_calls and tool tool_call_id. Never coerces
        missing content to the string \"None\".
        """
        if isinstance(msg, dict):
            role = msg.get("role", msg.get("type", "user"))
            content = msg.get("content")
            tool_calls = msg.get("tool_calls")
            tool_call_id = msg.get("tool_call_id")
            name = msg.get("name")
        else:
            role = getattr(msg, "type", None) or getattr(msg, "role", None) or "user"
            content = getattr(msg, "content", None)
            tool_calls = getattr(msg, "tool_calls", None)
            tool_call_id = getattr(msg, "tool_call_id", None)
            name = getattr(msg, "name", None)

        if role == "human":
            role = "user"
        elif role == "ai":
            role = "assistant"

        role_s = str(role)
        out: Dict[str, Any] = {"role": role_s}

        if role_s == "assistant":
            # content may be null when the model only emits tool_calls
            if content is None:
                out["content"] = None
            elif isinstance(content, (dict, list)):
                out["content"] = content
            else:
                out["content"] = str(content)
            if tool_calls:
                normalized_calls = []
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        fn = tc.get("function") or {}
                        args = fn.get("arguments") if isinstance(fn, dict) else None
                        if args is None:
                            args = tc.get("arguments")
                        if isinstance(args, (dict, list)):
                            args_s = json.dumps(args, ensure_ascii=False)
                        else:
                            args_s = str(args) if args is not None else "{}"
                        normalized_calls.append({
                            "id": tc.get("id") or "",
                            "type": tc.get("type") or "function",
                            "function": {
                                "name": (fn.get("name") if isinstance(fn, dict) else None) or tc.get("name") or "",
                                "arguments": args_s,
                            },
                        })
                    else:
                        fn = getattr(tc, "function", None)
                        args = getattr(fn, "arguments", None) if fn else getattr(tc, "arguments", "{}")
                        if isinstance(args, (dict, list)):
                            args_s = json.dumps(args, ensure_ascii=False)
                        else:
                            args_s = str(args) if args is not None else "{}"
                        normalized_calls.append({
                            "id": getattr(tc, "id", "") or "",
                            "type": "function",
                            "function": {
                                "name": (getattr(fn, "name", None) if fn else None) or getattr(tc, "name", "") or "",
                                "arguments": args_s,
                            },
                        })
                out["tool_calls"] = normalized_calls
        elif role_s == "tool":
            if content is None:
                out["content"] = ""
            elif isinstance(content, (dict, list)):
                out["content"] = json.dumps(content, ensure_ascii=False)
            else:
                out["content"] = str(content)
            if tool_call_id is not None:
                out["tool_call_id"] = str(tool_call_id)
            if name is not None:
                out["name"] = str(name)
        else:
            # user / system
            if content is None:
                out["content"] = ""
            elif isinstance(content, (dict, list)):
                out["content"] = content
            else:
                out["content"] = str(content)

        return out

    def _parse_tool_calls(self, message: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """Normalize provider tool_calls into structured {id, name, arguments} dicts."""
        raw = message.get("tool_calls")
        if not raw:
            return None
        parsed: List[Dict[str, Any]] = []
        for i, tc in enumerate(raw):
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            name = fn.get("name") if isinstance(fn, dict) else None
            if not name:
                name = tc.get("name") or ""
            args = fn.get("arguments") if isinstance(fn, dict) else None
            if args is None:
                args = tc.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    logger.warning("model_client.tool_args_json_failed", name=name, preview=args[:200] if args else "")
                    # keep the string so the loop can still surface it
            if not isinstance(args, dict):
                args = {"_raw": args} if args is not None else {}
            parsed.append({
                "id": tc.get("id") or f"call_{i}",
                "name": str(name or ""),
                "arguments": args,
            })
        return parsed or None
        
    def _parse_usage(self, raw: Dict[str, Any]) -> TokenUsage:
        usage = raw.get("usage", {})
        return TokenUsage(
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )

    def _cost_usd(self, usage: TokenUsage) -> Optional[float]:
        input_price = self.config.input_cost_per_1m
        output_price = self.config.output_cost_per_1m
        if input_price is None or output_price is None:
            return None
        return usage.total_cost(float(input_price), float(output_price))

    async def stream(
        self,
        messages: List[Any],
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        """Yield safe user-visible text deltas from an OpenAI-compatible SSE response."""
        payload_messages = []
        if system_prompt:
            payload_messages.append({"role": "system", "content": str(system_prompt)})
        payload_messages.extend(self._normalize_message(message) for message in messages)
        payload: dict[str, Any] = {
            "model": str(self.config.model),
            "messages": payload_messages,
            "stream": True,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        elif self.config.temperature is not None:
            payload["temperature"] = self.config.temperature
        max_t = max_tokens if max_tokens is not None else self.config.max_tokens
        if max_t is not None:
            payload["max_tokens"] = int(max(1, min(8192, max_t)))

        async with self._client.stream("POST", "/chat/completions", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    return
                try:
                    data = json.loads(raw)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                except (IndexError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if isinstance(content, str) and content:
                    yield content

    async def close(self):
        await self._client.aclose()


class KimiK2Client(BaseModelClient):
    async def chat_vision(self, *, prompt: str, screenshot_base64: str, max_tokens: int = 240) -> ModelResponse:
        """Submit one viewport image to Kimi using its documented multipart format."""
        if not self.config.api_key:
            raise RuntimeError("Kimi vision grounding requires the existing KIMI_API_KEY configuration.")
        start = time.perf_counter()
        payload = {
            "model": str(self.config.model),
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_base64}"}},
                {"type": "text", "text": prompt},
            ]}],
            "stream": False,
            "temperature": 1.0,
            "max_tokens": int(max(64, min(1024, max_tokens))),
        }
        resp = await self._client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("The Kimi vision model returned no usable grounding response.")
        usage = self._parse_usage(data)
        latency = (time.perf_counter() - start) * 1000
        logger.info("kimi_k26.vision_complete", model=payload["model"], input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, latency_ms=round(latency, 2))
        return ModelResponse(content=content, model=payload["model"], provider="kimi", usage=usage, latency_ms=latency, cost_usd=self._cost_usd(usage), raw=data)

    #@track(project_name="aether", name="kimi_k26_chat")
    async def chat(
        self,
        messages,
        system_prompt=None,
        temperature=None,
        max_tokens=None,
        response_schema: Optional[Dict[str, Any]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
    ):
        start = time.perf_counter()
        
        # Build messages list - ensure proper format (preserves tool turns)
        payload_messages = []
        if system_prompt:
            payload_messages.append({"role": "system", "content": str(system_prompt)})
        for msg in messages:
            payload_messages.append(self._normalize_message(msg))

        # Build payload with correct parameter names and types
        payload = {
            "model": str(self.config.model),
            "messages": payload_messages,
            "stream": False,
        }

        # Kimi k2.6 model only accepts temperature=1, so we always use 1.0
        payload["temperature"] = 1.0
        
        # Add max_tokens if set (must be positive integer)
        max_t = max_tokens if max_tokens is not None else self.config.max_tokens
        if max_t is not None:
            payload["max_tokens"] = int(max(1, min(8192, max_t)))

        if tools:
            payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice

        payload = self._with_structured_output(payload, response_schema)

        # Retry logic with exponential backoff
        max_retries = 3
        base_delay = 1.0
        last_error = None
        structured_rejected = False

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.info("kimi_k26.retry", attempt=attempt, delay=delay, model=payload["model"])
                    await asyncio.sleep(delay)
                
                resp = await self._client.post("/chat/completions", json=payload)
                
                # Log full request for debugging
                if resp.status_code >= 400:
                    logger.error(
                        "kimi_k26.error",
                        status_code=resp.status_code,
                        model=payload["model"],
                        attempt=attempt,
                    )
                
                # Handle specific error codes
                if resp.status_code == 401:
                    logger.error("kimi_k26.auth_error", status_code=resp.status_code)
                    resp.raise_for_status()
                elif resp.status_code == 429:
                    # Rate limit - retry with longer delay
                    if attempt < max_retries - 1:
                        continue
                    resp.raise_for_status()
                elif resp.status_code in {400, 404, 422} and response_schema and not structured_rejected:
                    # The provider does not accept the schema constraint. Replay
                    # the identical request without it rather than losing the call.
                    structured_rejected = True
                    payload = {
                        key: value for key, value in payload.items() if key != "response_format"
                    }
                    logger.info(
                        "kimi_k26.structured_output_unsupported",
                        status_code=resp.status_code,
                        model=payload["model"],
                    )
                    continue
                elif resp.status_code >= 400:
                    resp.raise_for_status()
                
                data = resp.json()
                break
                
            except Exception as e:
                last_error = e
                if attempt == max_retries - 1:
                    logger.error(
                        "kimi_k26.max_retries_exceeded",
                        error_type=type(e).__name__,
                        model=payload["model"],
                        attempts=max_retries,
                    )
                    raise
                continue

        usage = self._parse_usage(data)
        latency = (time.perf_counter() - start) * 1000
        message = data.get("choices", [{}])[0].get("message") or {}
        tool_calls = self._parse_tool_calls(message)
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            content = str(content)

        logger.info("kimi_k26.complete", model=payload["model"],
                    input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                    latency_ms=round(latency, 2),
                    tool_call_count=len(tool_calls or []))

        return ModelResponse(
            content=content,
            model=payload["model"], provider="kimi",
            usage=usage, latency_ms=latency, cost_usd=self._cost_usd(usage), raw=data,
            tool_calls=tool_calls,
        )


class DeepSeekClient(BaseModelClient):
    #@track(project_name="aether", name="deepseek_chat")
    async def chat(
        self,
        messages,
        system_prompt=None,
        temperature=None,
        max_tokens=None,
        response_schema: Optional[Dict[str, Any]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
    ):
        start = time.perf_counter()
        payload_messages = [self._normalize_message(m) for m in messages]
        if system_prompt:
            payload_messages.insert(0, {"role": "system", "content": system_prompt})

        payload = {
            "model": self.config.model,
            "messages": payload_messages,
            "stream": False,
        }

        temp = temperature if temperature is not None else self.config.temperature
        if temp is not None:
            payload["temperature"] = temp
        max_t = max_tokens if max_tokens is not None else self.config.max_tokens
        if max_t is not None:
            # deepseek-chat rejects output budgets above 8192.
            payload["max_tokens"] = int(max(1, min(8192, max_t)))

        if tools:
            payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice

        payload = self._with_structured_output(payload, response_schema)

        last_error: Exception | None = None
        structured_rejected = False
        for attempt in range(3):
            try:
                if attempt:
                    await asyncio.sleep(2 ** (attempt - 1))
                resp = await self._client.post("/chat/completions", json=payload)
                # Authentication and validation errors are deterministic: retrying
                # only hides a configuration problem from the task error surface.
                if resp.status_code in {400, 401, 403} and response_schema and not structured_rejected and resp.status_code == 400:
                    structured_rejected = True
                    payload = {
                        key: value for key, value in payload.items() if key != "response_format"
                    }
                    logger.info(
                        "deepseek.structured_output_unsupported",
                        status_code=resp.status_code,
                        model=payload["model"],
                    )
                    continue
                if resp.status_code in {400, 401, 403}:
                    resp.raise_for_status()
                if resp.status_code == 429 or resp.status_code >= 500:
                    resp.raise_for_status()
                resp.raise_for_status()
                data = resp.json()
                message = data.get("choices", [{}])[0].get("message") or {}
                content = message.get("content")
                tool_calls_preview = message.get("tool_calls")
                if (not isinstance(content, str) or not content.strip()) and not tool_calls_preview:
                    raise ValueError("The fallback model returned no usable message content.")
                break
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                logger.warning(
                    "deepseek.retryable_failure",
                    attempt=attempt + 1,
                    model=payload["model"],
                    error_type=type(exc).__name__,
                )
                if attempt == 2 or (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {400, 401, 403}):
                    raise
        else:  # Defensive: the loop always breaks or raises.
            raise last_error or RuntimeError("Fallback model failed without an error detail.")

        usage = self._parse_usage(data)
        latency = (time.perf_counter() - start) * 1000
        message = data.get("choices", [{}])[0].get("message") or {}
        tool_calls = self._parse_tool_calls(message)
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            content = str(content)

        logger.info("deepseek.complete", model=payload["model"],
                    input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                    latency_ms=round(latency, 2),
                    tool_call_count=len(tool_calls or []))

        return ModelResponse(
            content=content,
            model=payload["model"], provider="deepseek",
            usage=usage, latency_ms=latency, cost_usd=self._cost_usd(usage), raw=data,
            tool_calls=tool_calls,
        )


class NvidiaClient(BaseModelClient):
    async def chat(
        self,
        messages,
        system_prompt=None,
        temperature=None,
        max_tokens=None,
        response_schema: Optional[Dict[str, Any]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
    ):
        start = time.perf_counter()
        payload_messages = []
        if system_prompt:
            payload_messages.append({"role": "system", "content": str(system_prompt)})
        for msg in messages:
            payload_messages.append(self._normalize_message(msg))
        payload = {
            "model": str(self.config.model),
            "messages": payload_messages,
            "stream": False,
        }

        model_name = str(self.config.model).lower()
        max_t = max_tokens if max_tokens is not None else self.config.max_tokens
        if model_name.startswith("gpt-5"):
            payload["max_completion_tokens"] = int(max_t) if max_t is not None else 16384
            payload["reasoning"] = {"effort": "high"}
        elif model_name.startswith("claude-opus-4-7"):
            # Opus 4.7 uses adaptive thinking; do not send obsolete fixed-budget thinking fields.
            payload["max_tokens"] = int(max_t) if max_t is not None else 16384
        else:
            temp = temperature if temperature is not None else self.config.temperature
            if temp is not None:
                payload["temperature"] = temp
            if max_t is not None:
                payload["max_tokens"] = int(max_t)

        if tools:
            payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice

        payload = self._with_structured_output(payload, response_schema)

        try:
            resp = await self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            if response_schema and isinstance(exc, httpx.HTTPStatusError) and exc.response is not None and exc.response.status_code in {400, 404, 422}:
                # Schema constraints are a provider capability, not a task
                # requirement. Fall back to the unconstrained request.
                payload = {key: value for key, value in payload.items() if key != "response_format"}
                logger.info(
                    "nvidia.structured_output_unsupported",
                    status_code=exc.response.status_code,
                    model=payload["model"],
                )
                resp = await self._client.post("/chat/completions", json=payload)
            else:
                raise
        resp.raise_for_status()
        data = resp.json()

        usage = self._parse_usage(data)
        latency = (time.perf_counter() - start) * 1000

        message = data["choices"][0]["message"]
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            content = str(content)
        tool_calls = self._parse_tool_calls(message)
        reasoning = message.get("reasoning_content")

        logger.info("nvidia.complete", model=payload["model"],
                    input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                    latency_ms=round(latency, 2), has_reasoning=bool(reasoning),
                    tool_call_count=len(tool_calls or []))

        return ModelResponse(
            content=content,
            model=payload["model"], provider="nvidia",
            usage=usage, latency_ms=latency, cost_usd=self._cost_usd(usage), raw=data,
            tool_calls=tool_calls,
        )

    async def chat_vision(self, *, prompt: str, screenshot_base64: str, max_tokens: int = 240) -> ModelResponse:
        """Submit one viewport image through the NVIDIA Kimi K3 vision-capable endpoint."""
        if not self.config.api_key:
            raise RuntimeError("Vision grounding requires an API key for the vision provider.")
        start = time.perf_counter()
        payload = {
            "model": str(self.config.model),
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_base64}"}},
                {"type": "text", "text": prompt},
            ]}],
            "stream": False,
            "temperature": 1.0,
            "max_tokens": int(max(64, min(16384, max_tokens))),
        }
        resp = await self._client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("The vision model returned no usable grounding response.")
        usage = self._parse_usage(data)
        latency = (time.perf_counter() - start) * 1000
        logger.info("nvidia.vision_complete", model=payload["model"], input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, latency_ms=round(latency, 2))
        return ModelResponse(content=content, model=payload["model"], provider="nvidia", usage=usage, latency_ms=latency, cost_usd=self._cost_usd(usage), raw=data)


class ModelRouter:
    ROLE_PLANNING = "planning"
    ROLE_LOOP = "loop"
    ROLE_INTENT_EXTRACTION = "intent_extraction"
    ROLE_STEP_ADJUSTMENT = "step_adjustment"
    ROLE_VERIFICATION = "verification"
    ROLE_FALLBACK = "fallback"

    def __init__(self):
        """Build independent model lanes for planning and vision.

        Planning: NVIDIA Nemotron 3.5 (PLANNER_MODEL) — tool workflow generation.
        Vision:     NVIDIA Kimi K3 (NVIDIA_MODEL)         — image/video understanding.
        Fallback:   DeepSeek via SYNTHAI_*_PROVIDER overrides.
        """
        kimi_k3_cfg = CONFIG.kimi_k3
        planner_cfg = CONFIG.planner
        kimi_cfg = CONFIG.kimi_k2
        if not kimi_cfg.model:
            kimi_cfg = dataclasses.replace(kimi_cfg, model="kimi-k2.6")
        ds_cfg = CONFIG.deepseek
        if ds_cfg.model and "flash" in ds_cfg.model.lower():
            logger.info("model_router.fixing_deepseek_model", old=ds_cfg.model, new="deepseek-chat")
            ds_cfg = dataclasses.replace(ds_cfg, model="deepseek-chat")

        self._planner: NvidiaClient | None = NvidiaClient(planner_cfg) if planner_cfg.api_key else None
        self._kimi: KimiK2Client | None = KimiK2Client(kimi_cfg) if kimi_cfg.api_key else None
        self._deepseek: DeepSeekClient | None = DeepSeekClient(ds_cfg) if ds_cfg.api_key else None
        self._nvidia: NvidiaClient | None = NvidiaClient(kimi_k3_cfg) if kimi_k3_cfg.api_key else None

        # Preserve development ergonomics while production configuration can
        # opt into one, two, or all three providers.
        available: dict[str, BaseModelClient] = {
            "planner": self._planner,
            "kimi": self._kimi,
            "deepseek": self._deepseek,
            "nvidia": self._nvidia,
        }
        available = {name: client for name, client in available.items() if client is not None}
        if not available:
            self._kimi = KimiK2Client(kimi_cfg)
            self._deepseek = DeepSeekClient(ds_cfg)
            available = {
                "kimi": self._kimi,
                "deepseek": self._deepseek,
            }

        def select_role(environment_name: str, preferred: tuple[str, ...]) -> BaseModelClient:
            requested = os.environ.get(environment_name, "").strip().lower()
            if requested and requested in available:
                return available[requested]
            if requested:
                logger.warning("model_router.requested_provider_unavailable", setting=environment_name, provider=requested)
            for provider in preferred:
                if provider in available:
                    return available[provider]
            return next(iter(available.values()))

        planning_client = select_role("SYNTHAI_PLANNING_PROVIDER", ("planner", "nvidia", "kimi", "deepseek"))
        verification_client = select_role("SYNTHAI_VERIFICATION_PROVIDER", ("kimi", "deepseek", "nvidia", "planner"))
        fallback_client = select_role("SYNTHAI_FAST_PROVIDER", ("deepseek", "kimi", "nvidia", "planner"))
        self._planning_override_client: BaseModelClient | None = None
        planning_override = str(getattr(CONFIG, "planning_model_override", "") or os.getenv("PLANNING_MODEL_OVERRIDE", "")).strip()
        if planning_override:
            override_config = dataclasses.replace(planning_client.config, model=planning_override)
            planning_client = type(planning_client)(override_config)
            self._planning_override_client = planning_client
            logger.info("model_router.planning_model_override", model=planning_override, provider=override_config.provider)

        self._available = available
        self._vision = self._nvidia
        role_defaults = {
            "fast": fallback_client,
            "planner": planning_client,
            "coder": planning_client,
            "researcher": planning_client,
            "browser": self._vision or verification_client,
            "document": self._vision or planning_client,
            "spreadsheet": self._vision or planning_client,
            "verifier": verification_client,
        }
        self._lane_clients: dict[str, BaseModelClient] = {}
        self._owned_lane_clients: list[BaseModelClient] = []
        for descriptor in LANE_DESCRIPTORS:
            requested_provider = os.getenv(descriptor.env_provider, "").strip().lower()
            base = available.get(requested_provider) or role_defaults[descriptor.lane.value]
            requested_model = os.getenv(descriptor.env_model, "").strip()
            client = base
            if requested_model and requested_model != str(base.config.model):
                client = type(base)(dataclasses.replace(base.config, model=requested_model))
                self._owned_lane_clients.append(client)
            self._lane_clients[descriptor.lane.value] = client
        self._role_map = {
            self.ROLE_PLANNING: planning_client,
            self.ROLE_INTENT_EXTRACTION: planning_client,
            self.ROLE_STEP_ADJUSTMENT: fallback_client,
            self.ROLE_VERIFICATION: verification_client,
            self.ROLE_FALLBACK: fallback_client,
            **self._lane_clients,
        }
        self._provider_failures: dict[str, int] = {}
        # Cached capability probe: not every chat() implementation accepts a
        # schema constraint, and probing per call would add overhead.
        self._structured_support: dict[int, tuple[Any, bool]] = {}
        self._provider_unavailable_until: dict[str, float] = {}
        self._provider_locks: dict[str, asyncio.Lock] = {
            name: asyncio.Lock() for name in self._available
        }
        logger.info(
            "model_router.initialized",
            planning_model=planning_client.config.model,
            planning_provider=planning_client.config.provider,
            verification_model=verification_client.config.model,
            verification_provider=verification_client.config.provider,
            fallback_model=fallback_client.config.model,
            fallback_provider=fallback_client.config.provider,
            vision_model=self._vision.config.model if self._vision else None,
            vision_provider=self._vision.config.provider if self._vision else None,
        )

    def lane_catalog(self) -> list[dict[str, Any]]:
        """Return lane contracts and the selected provider/model per lane."""
        catalog = lane_catalog()
        selected = {lane: {"provider": client.config.provider, "model": client.config.model} for lane, client in self._lane_clients.items()}
        return [{**item, "selected": selected.get(item["lane"])} for item in catalog]

    def client_for_lane(self, lane: str | ModelLane) -> BaseModelClient:
        """Return the client selected for a capability lane."""
        key = lane.value if isinstance(lane, ModelLane) else str(lane)
        try:
            return self._lane_clients[key]
        except KeyError as exc:
            raise ValueError(f"Unknown model lane: {lane}") from exc

    def _provider_name(self, client: BaseModelClient) -> str:
        return str(client.config.provider or "unknown").lower()

    def _provider_healthy(self, client: BaseModelClient) -> bool:
        return time.monotonic() >= self._provider_unavailable_until.get(self._provider_name(client), 0.0)

    def _mark_success(self, client: BaseModelClient) -> None:
        name = self._provider_name(client)
        self._provider_failures.pop(name, None)
        self._provider_unavailable_until.pop(name, None)

    def _mark_failure(self, client: BaseModelClient, exc: Exception) -> None:
        name = self._provider_name(client)
        failures = self._provider_failures.get(name, 0) + 1
        self._provider_failures[name] = failures
        # Short cooldowns avoid repeatedly paying for a dead provider while
        # allowing recovery without restarting the application.
        cooldown = min(60.0, 2.0 ** min(failures, 5))
        if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None and exc.response.status_code in {400, 401, 403}:
            cooldown = 300.0
        self._provider_unavailable_until[name] = time.monotonic() + cooldown
        logger.warning(
            "model_router.provider_unavailable",
            provider=name,
            failure_count=failures,
            cooldown_seconds=round(cooldown, 1),
            error_type=type(exc).__name__,
        )

    def _fallback_clients(self, primary: BaseModelClient) -> list[BaseModelClient]:
        return [
            self._available[name]
            for name in ("kimi", "deepseek", "nvidia")
            if name in self._available
            and self._available[name] is not primary
            and self._provider_healthy(self._available[name])
        ]

    async def route(
        self,
        role: str,
        messages: List[Any],
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
    ) -> ModelResponse:
        resolved = role
        if role not in self._role_map:
            aliases = {
                "loop": "planning",
                "agent": "planning",
                "executor": "planning",
                "worker": "planning",
                "vision": "planning",
                "verifier": "verification",
                "summarizer": "verification",
                "intent_extraction": getattr(self, "ROLE_INTENT_EXTRACTION", "planning"),
                "step_adjustment": getattr(self, "ROLE_STEP_ADJUSTMENT", "planning"),
            }
            resolved = aliases.get(role, "planning")
            if resolved not in self._role_map:
                # last resort: first available client
                resolved = next(iter(self._role_map), role)
        client = self._role_map.get(resolved)
        if not client:
            raise ValueError(f"Unknown role: {role}")
        try:
            deadline = max(5.0, float(os.getenv("MODEL_CALL_DEADLINE_SECONDS", "45")))
        except (TypeError, ValueError):
            deadline = 45.0
        candidates = [client] + self._fallback_clients(client)
        last_error: Exception | None = None
        with tracing.span(
            "model.route",
            kind="model_call",
            role=role,
            structured=bool(response_schema),
            has_tools=bool(tools),
            candidates=[self._provider_name(candidate) for candidate in candidates],
        ) as active_span:
            for candidate in candidates:
                if candidate is not client and not self._provider_healthy(candidate):
                    continue
                try:
                    call_started = time.perf_counter()
                    metrics.inc("onsite48_model_calls_total", labels={"role": role, "provider": self._provider_name(candidate)})
                    response = await asyncio.wait_for(
                        self._call_candidate(
                            candidate, messages, system_prompt, temperature, max_tokens,
                            response_schema, tools=tools, tool_choice=tool_choice,
                        ),
                        timeout=deadline if candidate is client else min(deadline, 20.0),
                    )
                    self._mark_success(candidate)
                    metrics.observe("onsite48_model_latency_seconds", time.perf_counter() - call_started, labels={"role": role, "provider": self._provider_name(candidate)})
                    if response.cost_usd is not None:
                        metrics.inc("onsite48_model_cost_usd_total", float(response.cost_usd), labels={"role": role, "provider": self._provider_name(candidate)})
                    self._annotate_model_span(active_span, response, candidate, role)
                    return response
                except Exception as exc:
                    last_error = exc
                    self._mark_failure(candidate, exc)
                    metrics.inc("onsite48_model_failures_total", labels={"role": role, "provider": self._provider_name(candidate), "error": type(exc).__name__})
                    tracing.record_event(
                        "model.provider_failed",
                        kind="model_error",
                        role=role,
                        provider=self._provider_name(candidate),
                        error_type=type(exc).__name__,
                        error=str(exc)[:300],
                    )
            raise last_error or RuntimeError("No healthy model provider is available.")

    def _annotate_model_span(self, active_span, response: ModelResponse, candidate, role: str) -> None:
        usage = getattr(response, "usage", None)
        content = getattr(response, "content", "") or ""
        attributes = {
            "provider": getattr(candidate.config, "provider", None),
            "model": getattr(response, "model", None),
            "role": role,
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "total_tokens": (getattr(usage, "input_tokens", 0) or 0) + (getattr(usage, "output_tokens", 0) or 0),
            "cost_usd": getattr(response, "cost_usd", None),
            "provider_latency_ms": getattr(response, "latency_ms", None),
            "response_chars": len(content),
        }
        # Model output routinely contains customer email bodies, quoted records
        # and other personal data, and redaction only masks secret-shaped keys.
        # Content is therefore metadata-off by default: enabling it is an
        # explicit decision about retention and export destination, not a
        # default. Without this flag the trace store becomes a plain-text copy
        # of everything the agent has read.
        if _trace_content_enabled():
            attributes["response_preview"] = content[:2000]
        active_span.update(attributes)

    async def _call_candidate(
        self,
        candidate,
        messages,
        system_prompt,
        temperature,
        max_tokens,
        response_schema,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
    ) -> ModelResponse:
        """Call a provider, passing tools and schema constraints when supported."""
        kwargs: Dict[str, Any] = {}
        if response_schema and self._client_supports_structured_output(candidate):
            kwargs["response_schema"] = response_schema
        # Always forward tools when present; clients now accept the kwargs.
        if tools:
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        return await candidate.chat(
            messages, system_prompt, temperature, max_tokens, **kwargs
        )

    def _client_supports_structured_output(self, candidate) -> bool:
        # The candidate is stored alongside the verdict so a recycled id() can
        # never be matched against a different client.
        cached = self._structured_support.get(id(candidate))
        if cached is not None and cached[0] is candidate:
            return cached[1]
        try:
            parameters = inspect.signature(candidate.chat).parameters
            supported = "response_schema" in parameters or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            )
        except (TypeError, ValueError):
            supported = False
        self._structured_support[id(candidate)] = (candidate, supported)
        return supported

    async def route_vision(self, *, prompt: str, screenshot_base64: str, max_tokens: int = 240) -> ModelResponse:
        """Vision grounding requires an active vision provider."""
        if not self._vision:
            raise RuntimeError("Vision grounding is unavailable because no vision provider is configured.")
        return await self._vision.chat_vision(prompt=prompt, screenshot_base64=screenshot_base64, max_tokens=max_tokens)

    async def stream(
        self,
        role: str,
        messages: List[Any],
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream from one healthy provider; retry only before any token is emitted."""
        resolved = role
        if role not in self._role_map:
            aliases = {"loop": "planning", "agent": "planning", "executor": "planning",
                       "intent_extraction": getattr(self, "ROLE_INTENT_EXTRACTION", "planning"),
                       "step_adjustment": getattr(self, "ROLE_STEP_ADJUSTMENT", "planning")}
            resolved = aliases.get(role, "planning")
            if resolved not in self._role_map:
                # last resort: first available client
                resolved = next(iter(self._role_map), role)
        client = self._role_map.get(resolved)
        if not client:
            raise ValueError(f"Unknown role: {role}")
        try:
            deadline = max(5.0, float(os.getenv("MODEL_CALL_DEADLINE_SECONDS", "45")))
        except (TypeError, ValueError):
            deadline = 45.0
        candidates = [client] + self._fallback_clients(client)
        last_error: Exception | None = None
        for candidate in candidates:
            if candidate is not client and not self._provider_healthy(candidate):
                continue
            emitted = False
            try:
                iterator = candidate.stream(messages, system_prompt, temperature, max_tokens)
                while True:
                    try:
                        chunk = await asyncio.wait_for(iterator.__anext__(), timeout=deadline)
                    except StopAsyncIteration:
                        break
                    emitted = True
                    yield chunk
                self._mark_success(candidate)
                return
            except Exception as exc:
                last_error = exc
                self._mark_failure(candidate, exc)
                if emitted:
                    raise
        raise last_error or RuntimeError("No healthy streaming provider is available.")

    async def close(self):
        closed: set[int] = set()
        for client in (*self._owned_lane_clients, self._kimi, self._nvidia, self._deepseek, self._vision, self._planning_override_client):
            if client and id(client) not in closed:
                closed.add(id(client))
                await client.close()

KimiDeepSeekRouter = ModelRouter