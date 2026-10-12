import asyncio
import dataclasses
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx
import structlog
from opik import track

from app.core.config import CONFIG, ModelConfig

logger = structlog.get_logger("aether.model_router")

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
    raw: Optional[Dict[str, Any]] = None
    cost_usd: Optional[float] = None
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
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
    ) -> ModelResponse:
        pass

    def _normalize_message(self, msg: Any) -> Dict[str, Any]:
        """Serialize a message for OpenAI-compatible chat completions."""
        if isinstance(msg, dict):
            role = msg.get("role", msg.get("type", "user"))
            content = msg.get("content")
            tool_calls = msg.get("tool_calls")
            tool_call_id = msg.get("tool_call_id")
            name = msg.get("name")
        else:
            role = getattr(msg, "role", None) or getattr(msg, "type", None) or "user"
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
            out["content"] = None if content is None else (
                content if isinstance(content, (dict, list)) else str(content)
            )
            if tool_calls:
                normalized_calls = []
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        fn = tc.get("function") or {}
                        args = fn.get("arguments") if isinstance(fn, dict) else tc.get("arguments")
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
            if content is None:
                out["content"] = ""
            elif isinstance(content, (dict, list)):
                out["content"] = content
            else:
                out["content"] = str(content)
        return out

    def _parse_usage(self, raw: Dict[str, Any]) -> TokenUsage:
        usage = raw.get("usage", {})
        return TokenUsage(
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )

    async def close(self):
        await self._client.aclose()


class KimiK2Client(BaseModelClient):
    #@track(project_name="aether", name="kimi_k26_chat")
    async def chat(self, messages, system_prompt=None, temperature=None, max_tokens=None):
        start = time.perf_counter()
        
        # Build messages list - ensure proper format
        payload_messages = []
        if system_prompt:
            payload_messages.append({"role": "system", "content": str(system_prompt)})
        for msg in messages:
            normalized = self._normalize_message(msg)
            payload_messages.append(normalized)

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

        # Retry logic with exponential backoff
        max_retries = 3
        base_delay = 1.0
        last_error = None
        
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
                elif resp.status_code >= 400:
                    resp.raise_for_status()
                
                data = resp.json()
                break
                
            except Exception as e:
                last_error = e
                if attempt == max_retries - 1:
                    logger.error("kimi_k26.max_retries_exceeded", 
                                error=str(e), 
                                model=payload["model"],
                                attempts=max_retries)
                    raise
                continue

        usage = self._parse_usage(data)
        latency = (time.perf_counter() - start) * 1000

        logger.info("kimi_k26.complete", model=payload["model"],
                    input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                    latency_ms=round(latency, 2))

        return ModelResponse(
            content=data["choices"][0]["message"]["content"],
            model=payload["model"], provider="kimi",
            usage=usage, latency_ms=latency, raw=data,
        )


class DeepSeekClient(BaseModelClient):
    #@track(project_name="aether", name="deepseek_chat")
    async def chat(self, messages, system_prompt=None, temperature=None, max_tokens=None):
        start = time.perf_counter()
        payload_messages = [self._normalize_message(msg) for msg in messages]
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
            payload["max_tokens"] = max_t

        resp = await self._client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        usage = self._parse_usage(data)
        latency = (time.perf_counter() - start) * 1000

        logger.info("deepseek.complete", model=payload["model"],
                    input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                    latency_ms=round(latency, 2))

        return ModelResponse(
            content=data["choices"][0]["message"]["content"],
            model=payload["model"], provider="deepseek",
            usage=usage, latency_ms=latency, raw=data,
        )


class ModelRouter:
    ROLE_PLANNING = "planning"
    ROLE_VERIFICATION = "verification"
    ROLE_FALLBACK = "fallback"
    ROLE_LOOP = "loop"
    # Aliases used by runtime / intent paths
    ROLE_ALIASES = {
        "loop": "planning",
        "agent": "planning",
        "executor": "planning",
        "intent_extraction": "planning",
        "step_adjustment": "planning",
    }

    def __init__(self):
        # -------------------------------------------------------
        # Kimi model configuration - uses kimi-latest by default.
        # You can override via environment: KIMI_MODEL=kimi-latest
        # -------------------------------------------------------
        kimi_cfg = CONFIG.kimi_k2
        if not kimi_cfg.model:
            logger.info("model_router.using_default_kimi_model", model="kimi-latest")
            kimi_cfg = dataclasses.replace(kimi_cfg, model="kimi-latest")

        self._kimi = KimiK2Client(kimi_cfg)
        logger.info(
            "model_router.kimi_config",
            model=kimi_cfg.model,
            base_url=kimi_cfg.base_url,
            configured=bool(kimi_cfg.api_key),
        )
        ds_cfg = CONFIG.deepseek
        if not ds_cfg.model or "flash" in ds_cfg.model.lower():
            logger.info("model_router.fixing_deepseek_model", old=ds_cfg.model, new="deepseek-chat")
            ds_cfg = dataclasses.replace(ds_cfg, model="deepseek-chat")
        self._deepseek = DeepSeekClient(ds_cfg)
        self._role_map = {
            self.ROLE_PLANNING: self._kimi,
            self.ROLE_VERIFICATION: self._deepseek,
            self.ROLE_FALLBACK: self._deepseek,
        }
        logger.info("model_router.initialized")

    def _resolve_role(self, role: str) -> str:
        if role in self._role_map:
            return role
        aliases = getattr(self, "ROLE_ALIASES", {}) or {}
        mapped = aliases.get(role)
        if mapped and mapped in self._role_map:
            return mapped
        # Default unknown roles to planning so runtime "loop" never hard-fails
        if self.ROLE_PLANNING in self._role_map:
            return self.ROLE_PLANNING
        raise ValueError(f"Unknown role: {role}")

    async def route(self, role: str, messages: List[Any],
                    system_prompt: Optional[str] = None,
                    temperature: Optional[float] = None,
                    max_tokens: Optional[int] = None,
                    tools: Optional[List[Any]] = None,
                    tool_choice: Optional[str] = None,
                    **kwargs: Any) -> ModelResponse:
        resolved = self._resolve_role(role)
        client = self._role_map.get(resolved)
        if not client:
            raise ValueError(f"Unknown role: {role}")

        logger.info("model_router.route", role=role, resolved=resolved, provider=client.config.provider, has_tools=bool(tools))

        try:
            # Always attempt to pass tools / tool_choice when provided.
            # Prefer native tool-calling; fall back without tools on TypeError
            # (client signature rejects unexpected kwargs) without claiming native_tools.
            if tools and hasattr(client, "chat"):
                try:
                    return await client.chat(
                        messages, system_prompt, temperature, max_tokens,
                        tools=tools, tool_choice=tool_choice or "auto",
                    )
                except TypeError:
                    logger.info(
                        "model_router.tools_unsupported",
                        role=role,
                        resolved=resolved,
                        provider=getattr(getattr(client, "config", None), "provider", None),
                    )
            return await client.chat(messages, system_prompt, temperature, max_tokens)
        except Exception as e:
            if resolved == self.ROLE_PLANNING:
                logger.warning("model_router.fallback", error=str(e))
                return await self._deepseek.chat(messages, system_prompt, temperature, max_tokens)
            raise

    async def close(self):
        await self._kimi.close()
        await self._deepseek.close()