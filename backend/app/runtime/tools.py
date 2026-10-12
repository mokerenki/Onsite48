"""Tool contract for the Phase-1 agent loop.

Wraps every existing tool in tools/registry.py into a ToolSpec without changing
tool behavior. Classification is honest: send = external_send + non_idempotent;
draft = write; search = read; content-returning tools = untrusted.
"""
from __future__ import annotations
import http.server
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional
import structlog
from app.tools.registry import BaseTool, ToolRegistry

logger = structlog.get_logger("onsite48.runtime.tools")
SideEffect = Literal["read", "write", "destructive", "external_send", "spend"]
Idempotency = Literal["idempotent", "keyed", "non_idempotent"]
Trust = Literal["trusted", "untrusted"]
ToolCategory = Literal[
    "filesystem", "web", "browser", "computer", "code", "data", "communication",
    "search", "deployment", "analysis", "visualization", "skill", "spawn", "system"
]

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    side_effect: SideEffect
    idempotency: Idempotency
    timeout_s: int
    max_output_chars: int
    trust: Trust
    category: ToolCategory = "system"
    readback: Callable | None = None
    resources: Callable | None = None
    _execute: Callable | None = field(default=None, repr=False, compare=False)
    _approval_required: bool = field(default=False, repr=False, compare=False)
    # Usage analytics
    _call_count: int = field(default=0, repr=False, compare=False)
    _success_count: int = field(default=0, repr=False, compare=False)
    _error_count: int = field(default=0, repr=False, compare=False)
    _total_latency_ms: float = field(default=0.0, repr=False, compare=False)

    def to_openai_tool(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}
    
    def record_call(self, success: bool, latency_ms: float) -> None:
        """Record tool usage for analytics."""
        object.__setattr__(self, '_call_count', self._call_count + 1)
        if success:
            object.__setattr__(self, '_success_count', self._success_count + 1)
        else:
            object.__setattr__(self, '_error_count', self._error_count + 1)
        object.__setattr__(self, '_total_latency_ms', self._total_latency_ms + latency_ms)
    
    def get_usage_stats(self) -> dict[str, Any]:
        """Get tool usage statistics."""
        avg_latency = self._total_latency_ms / self._call_count if self._call_count > 0 else 0
        success_rate = self._success_count / self._call_count if self._call_count > 0 else 0
        stats = {
            "call_count": self._call_count,
            "success_count": self._success_count,
            "error_count": self._error_count,
            "success_rate": round(success_rate, 3),
            "avg_latency_ms": round(avg_latency, 2),
        }
        # Include classification info if available
        if hasattr(self, '_classification_confidence'):
            stats["classification_confidence"] = self._classification_confidence
        if hasattr(self, '_classification_reasons'):
            stats["classification_reasons"] = self._classification_reasons
        return stats

_EXTERNAL_SEND_NAMES = {"gmail_send", "email_send", "send_email", "slack_send", "slack_post", "whatsapp_send", "send_message"}
_DESTRUCTIVE_FRAGMENTS = ("delete", "remove", "revoke", "destroy", "drop", "purge")
_SPEND_FRAGMENTS = ("purchase", "pay", "charge", "transfer", "buy")
_READ_FRAGMENTS = ("search", "list", "get", "read", "fetch", "find", "inspect", "view", "query")
_DRAFT_FRAGMENTS = ("draft", "create_draft", "compose")
_CONTENT_RETURNING = {"web", "browser", "computer", "bridge", "email", "gmail", "search", "document", "library", "research"}

def _classify_side_effect(name: str, irreversible: bool, approval_required: bool | None, schema_idempotency: str | None = None) -> SideEffect:
    lowered = name.lower()
    if lowered in _EXTERNAL_SEND_NAMES or ("send" in lowered and "draft" not in lowered):
        return "external_send"
    if any(f in lowered for f in _SPEND_FRAGMENTS):
        return "spend"
    if irreversible or any(f in lowered for f in _DESTRUCTIVE_FRAGMENTS):
        return "destructive"
    if any(f in lowered for f in _READ_FRAGMENTS) and "create" not in lowered and "write" not in lowered:
        return "read"
    if any(f in lowered for f in _DRAFT_FRAGMENTS):
        return "write"
    if schema_idempotency in ("read_only", "idempotent") and not irreversible:
        return "read"
    return "write"

def _classify_idempotency(name: str, schema_idempotency: str | None, side_effect: SideEffect) -> Idempotency:
    if schema_idempotency in ("read_only", "idempotent"):
        return "idempotent"
    if schema_idempotency in ("keyed", "idempotent_keyed"):
        return "keyed"
    if side_effect == "read":
        return "idempotent"
    if side_effect in ("external_send", "destructive", "spend"):
        return "non_idempotent"
    return "non_idempotent"

def _classify_trust(name: str) -> Trust:
    lowered = name.lower()
    if any(frag in lowered for frag in _CONTENT_RETURNING):
        return "untrusted"
    if lowered in {
        "finish", "ask_user", "file_read", "file_write", "file_edit",
        "file_list", "file_grep", "python_exec", "shell_exec", "deliver_file",
        "load_skill", "current_time",
    }:
        return "trusted"
    return "untrusted"


# Confidence scoring for classifications
@dataclass
class ClassificationResult:
    """Result of tool classification with confidence score."""
    side_effect: SideEffect
    idempotency: Idempotency
    trust: Trust
    category: ToolCategory
    confidence: float  # 0.0 to 1.0
    reasons: list[str]


def _classify_with_confidence(name: str, irreversible: bool, approval_required: bool | None, 
                               schema_idempotency: str | None, resources: list[str], 
                               operations: list[str]) -> ClassificationResult:
    """Classify tool with confidence scoring based on multiple signals."""
    lowered = name.lower()
    reasons = []
    confidence = 1.0
    
    # Side effect classification with confidence
    if lowered in _EXTERNAL_SEND_NAMES or ("send" in lowered and "draft" not in lowered):
        side_effect = "external_send"
        reasons.append(f"Name matches external send pattern: {lowered}")
    elif any(f in lowered for f in _SPEND_FRAGMENTS):
        side_effect = "spend"
        reasons.append(f"Name contains spend fragment")
    elif irreversible or any(f in lowered for f in _DESTRUCTIVE_FRAGMENTS):
        side_effect = "destructive"
        reasons.append("Tool marked irreversible or contains destructive fragment")
    elif any(f in lowered for f in _READ_FRAGMENTS) and "create" not in lowered and "write" not in lowered:
        side_effect = "read"
        reasons.append("Name contains read fragment without create/write")
    elif any(f in lowered for f in _DRAFT_FRAGMENTS):
        side_effect = "write"
        reasons.append("Name contains draft fragment")
    elif schema_idempotency in ("read_only", "idempotent") and not irreversible:
        side_effect = "read"
        reasons.append("Schema declares read_only/idempotent")
    else:
        side_effect = "write"
        reasons.append("Default to write")
        confidence *= 0.7  # Lower confidence for default
    
    # Idempotency classification
    if schema_idempotency in ("read_only", "idempotent"):
        idempotency = "idempotent"
        reasons.append("Schema declares idempotent")
    elif schema_idempotency in ("keyed", "idempotent_keyed"):
        idempotency = "keyed"
        reasons.append("Schema declares keyed idempotency")
    elif side_effect == "read":
        idempotency = "idempotent"
        reasons.append("Read side effect implies idempotent")
    elif side_effect in ("external_send", "destructive", "spend"):
        idempotency = "non_idempotent"
        reasons.append(f"{side_effect} side effect implies non_idempotent")
    else:
        idempotency = "non_idempotent"
        reasons.append("Default to non_idempotent")
        confidence *= 0.8
    
    # Trust classification
    if any(frag in lowered for frag in _CONTENT_RETURNING):
        trust = "untrusted"
        reasons.append("Name matches content-returning pattern")
    elif lowered in {
        "finish", "ask_user", "file_read", "file_write", "file_edit",
        "file_list", "file_grep", "python_exec", "shell_exec", "deliver_file",
        "load_skill", "current_time",
    }:
        trust = "trusted"
        reasons.append("Name in trusted builtin list")
    else:
        trust = "untrusted"
        reasons.append("Default to untrusted")
        confidence *= 0.7
    
    # Category inference with confidence
    category = _infer_category(name, side_effect, resources, operations)
    if category != "system":
        reasons.append(f"Inferred category: {category}")
    else:
        confidence *= 0.6
        reasons.append("Default category: system")
    
    # Boost confidence based on schema signals
    if resources and operations:
        reasons.append(f"Has schema resources={resources}, operations={operations}")
        confidence = min(1.0, confidence * 1.1)
    
    if schema_idempotency:
        reasons.append(f"Schema idempotency: {schema_idempotency}")
        confidence = min(1.0, confidence * 1.05)
    
    if approval_required is not None:
        reasons.append(f"Approval required: {approval_required}")
        confidence = min(1.0, confidence * 1.05)
    
    return ClassificationResult(
        side_effect=side_effect,
        idempotency=idempotency,
        trust=trust,
        category=category,
        confidence=round(min(1.0, confidence), 3),
        reasons=reasons,
    )


def _parameters_as_json_schema(tool: BaseTool) -> dict:
    schema = tool.schema
    props = schema.parameters or {}
    normalized: dict[str, Any] = {}
    for key, val in props.items():
        if isinstance(val, dict) and "type" in val:
            normalized[key] = val
        else:
            normalized[key] = {"type": "string", "description": str(val) if val else key}
    return {"type": "object", "properties": normalized, "required": list(schema.required or []), "additionalProperties": False}

def _infer_readback_for_registry(name: str, side: SideEffect, tool: BaseTool) -> Callable | None:
    """Attach a best-effort readback for mutating registry tools.

    Uses the new strategy-based readback system for known tools, falls back to
    generic get-tool pattern for create/update/delete/send tools.
    """
    if side not in ("write", "destructive", "external_send", "spend"):
        return None
    lowered = name.lower()
    
    # Check if we have a dedicated strategy for this tool
    if name in _READBACK_STRATEGIES:
        async def _rb_strategy(**kwargs: Any) -> dict[str, Any]:
            prior = kwargs.pop("_prior_result", None) or {}
            # Merge prior result into args for verification
            verify_args = {**kwargs, **prior}
            return await execute_readback(name, verify_args, prior, **kwargs)
        return _rb_strategy
    
    # Map common write tools to their read counterparts
    pairs = [
        ("create_draft", "get_draft", "draft_id"),
        ("gmail_create_draft", "gmail_get_draft", "draft_id"),
        ("gmail_send", "gmail_get_message", "message_id"),
        ("send_email", "get_message", "message_id"),
        ("create_event", "get_event", "event_id"),
        ("calendar_create", "calendar_get", "event_id"),
        ("create_task", "get_task", "task_id"),
        ("update_event", "get_event", "event_id"),
        ("delete_event", "get_event", "event_id"),
        ("delete_draft", "get_draft", "draft_id"),
    ]
    get_name, id_arg = None, "id"
    for write_frag, get_frag, idk in pairs:
        if write_frag in lowered:
            get_name, id_arg = get_frag, idk
            break
    if get_name is None:
        # Generic: if tool name starts with create_/update_/delete_/send_, try get_
        for prefix in ("create_", "update_", "delete_", "send_"):
            if lowered.startswith(prefix):
                rest = name[len(prefix):]
                get_name = f"get_{rest}"
                break
    if get_name is None:
        async def _rb_unverified(**kwargs: Any) -> dict[str, Any]:
            return {
                "verified": False,
                "observed": {"tool": name},
                "expected_diff": "no readback counterpart registered; mutation unverified",
            }
        return _rb_unverified

    async def _rb(**kwargs: Any) -> dict[str, Any]:
        # Prefer id from prior result if the loop passed it via _prior_result
        prior = kwargs.pop("_prior_result", None) or {}
        lookup_val = None
        for key in (id_arg, "id", "draft_id", "event_id", "message_id", "task_id"):
            if isinstance(prior, dict) and prior.get(key):
                lookup_val = prior[key]
                break
            if kwargs.get(key):
                lookup_val = kwargs[key]
                break
        if lookup_val is None:
            return {
                "verified": False,
                "observed": {},
                "expected_diff": f"no {id_arg} in write result for readback via {get_name}",
            }
        # Attempt to call the get tool if available on the same registry object
        try:
            # tool may have a parent registry reference; best-effort
            default_registry = None
            try:
                from app.core import instances as _inst

                default_registry = getattr(_inst, "tool_registry", None)
                if default_registry is None:
                    from app.core.context import onsite48 as _ctx

                    default_registry = getattr(_ctx, "tool_registry", None)
            except Exception:
                default_registry = None
            get_tool = default_registry.get(get_name) if default_registry else None
            if get_tool is None:
                return {
                    "verified": False,
                    "observed": {"would_call": get_name, id_arg: lookup_val},
                    "expected_diff": f"get tool {get_name} not in registry",
                }
            observed = await get_tool.execute(**{id_arg: lookup_val})
            # Existence implies verified for create/send; for delete, absence is verified
            if "delete" in lowered:
                missing = bool(
                    isinstance(observed, dict)
                    and (observed.get("error_code") in ("not_found", "404") or observed.get("error"))
                )
                return {
                    "verified": missing,
                    "observed": observed if isinstance(observed, dict) else {"raw": str(observed)[:500]},
                    "expected_diff": None if missing else "resource still present after delete",
                }
            ok = not (isinstance(observed, dict) and observed.get("error_code"))
            return {
                "verified": ok,
                "observed": observed if isinstance(observed, dict) else {"raw": str(observed)[:500]},
                "expected_diff": None if ok else "get after write returned error",
            }
        except Exception as exc:
            return {
                "verified": False,
                "observed": {},
                "expected_diff": f"readback exception: {exc}",
            }

    return _rb


def _infer_category(name: str, side: SideEffect, resources: list[str], operations: list[str]) -> ToolCategory:
    """Infer tool category from name, side effect, resources, and operations."""
    lowered = name.lower()
    
    # Explicit category mappings
    if any(f in lowered for f in ("file_", "deliver_file")):
        return "filesystem"
    if any(f in lowered for f in ("shell_", "python_", "code_")):
        return "code"
    if any(f in lowered for f in ("browser", "bridge_", "computer_", "navigate", "click", "fill", "scroll", "inspect", "read_page", "locate")):
        return "browser"
    if any(f in lowered for f in ("search", "web_", "tavily", "fetch", "http_")):
        return "search"
    if any(f in lowered for f in ("deploy", "publish", "package_", "preview_", "create_website", "screenshot")):
        return "deployment"
    if any(f in lowered for f in ("render_preview", "chart", "visualization", "screenshot")):
        return "visualization"
    if any(f in lowered for f in ("load_skill",)):
        return "skill"
    if any(f in lowered for f in ("spawn", "wide_map", "await_")):
        return "spawn"
    if any(f in lowered for f in ("email", "gmail", "slack", "whatsapp", "send", "message", "calendar", "document", "library")):
        return "communication"
    if any(f in lowered for f in ("data", "analy", "research", "chart")):
        return "analysis"
    if any(f in lowered for f in ("finish", "ask_user", "current_time", "time")):
        return "system"
    
    # Infer from resources/operations
    if "web_page" in resources or "browser" in resources:
        return "browser"
    if "code" in resources:
        return "code"
    if "document" in resources or "library" in resources:
        return "data"
    if "chart" in resources:
        return "visualization"
    if side == "external_send":
        return "communication"
    
    return "system"


def wrap_tool(tool: BaseTool) -> ToolSpec:
    schema = tool.schema
    classification = _classify_with_confidence(
        schema.name, schema.irreversible, schema.approval_required, 
        schema.idempotency, schema.resources, schema.operations
    )
    approval = bool(schema.approval_required if schema.approval_required is not None else schema.irreversible or classification.side_effect in ("external_send", "destructive", "spend"))
    timeout = int(schema.timeout_seconds or 60)
    async def _exec(**kwargs: Any) -> Any:
        return await tool.execute(**kwargs)
    desc = schema.description or f"Tool {schema.name}"
    if "send" in schema.name.lower() and "draft" not in schema.name.lower():
        if "Do not use for drafts" not in desc:
            desc = desc.rstrip(".") + ". Use only to actually send; prefer draft tools when the user has not confirmed send."
    rb = _infer_readback_for_registry(schema.name, classification.side_effect, tool)
    spec = ToolSpec(name=schema.name, description=desc, parameters=_parameters_as_json_schema(tool), side_effect=classification.side_effect, idempotency=classification.idempotency, timeout_s=timeout, max_output_chars=12000, trust=classification.trust, category=classification.category, readback=rb, _execute=_exec, _approval_required=approval)
    # Store classification confidence for debugging/analysis
    object.__setattr__(spec, '_classification_confidence', classification.confidence)
    object.__setattr__(spec, '_classification_reasons', classification.reasons)
    return spec

def _builtin_finish() -> ToolSpec:
    return ToolSpec(
        name="finish",
        description=(
            "End the run with the user-facing answer. Call only when the goal is complete or cannot be completed. "
            "The summary field is shown directly to the user: when they asked to list/show/enumerate items "
            "(emails, drafts, events, etc.), include the actual items (bullet or numbered lines with the "
            "requested fields), not a description of having listed them. For counts, state the number. "
            "For long reports or tables, deliver a file and summarize with a pointer. "
            "Provide evidence_refs for any claims that came from tools."
        ),
        parameters={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "User-facing answer. For list/show requests, put the real items here "
                        "(one line per item). Do not only say that you listed them."
                    ),
                },
                "evidence_refs": {"type": "array", "items": {"type": "string"}},
                "status": {
                    "type": "string",
                    "enum": [
                        "completed",
                        "completed_unverified",
                        "partial",
                        "needs_input",
                        "waiting_approval",
                        "failed",
                        "cancelled",
                    ],
                },
            },
            "required": ["summary", "status"],
            "additionalProperties": False,
        },
        side_effect="read",
        idempotency="idempotent",
        timeout_s=5,
        max_output_chars=12000,
        trust="trusted",
        category="system",
        _execute=None,
        _approval_required=False,
    )

def _builtin_ask_user() -> ToolSpec:
    return ToolSpec(name="ask_user", description="Ask the user a clarifying question and wait for a reply.", parameters={"type": "object", "properties": {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}}, "required": ["question"], "additionalProperties": False}, side_effect="read", idempotency="idempotent", timeout_s=5, max_output_chars=2000, trust="trusted", category="system", _execute=None, _approval_required=False)

def _run_id_from_kwargs(kwargs: dict) -> str:
    return (
        kwargs.get("run_id")
        or kwargs.get("task_id")
        or kwargs.get("thread_id")
        or os.environ.get("AGENT_RUN_ID")
        or "default"
    )


def _get_ws(kwargs: dict):
    from app.runtime.workspace import get_workspace, create_workspace
    import asyncio
    run_id = _run_id_from_kwargs(kwargs)
    ws = get_workspace(run_id)
    # Ensure created (idempotent)
    if not getattr(ws, "_created", False):
        # create is async; schedule if needed
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # caller is already in async context; we rely on tools being awaited
                pass
            else:
                loop.run_until_complete(ws.create())
        except Exception:
            pass
    return ws



# ---------------------------------------------------------------------------
# Read-back helpers (Phase 3): after a mutation, re-observe the world.
# ---------------------------------------------------------------------------

async def _readback_file_write(path: str, content: str | None = None, **kwargs: Any) -> dict[str, Any]:
    """Re-read the file and compare size/hash to expected content."""
    import hashlib
    ws = _get_ws(kwargs)
    if not getattr(ws, "_created", False):
        await ws.create()
    observed = await ws.read(path)
    if observed.get("error_code"):
        return {"verified": False, "observed": observed, "expected_diff": "file missing or unreadable after write"}
    obs_content = observed.get("content")
    if obs_content is None and observed.get("content_b64"):
        import base64
        obs_bytes = base64.b64decode(observed["content_b64"])
        obs_content = obs_bytes  # type: ignore
    expected = content if content is not None else kwargs.get("content")
    if expected is None:
        return {"verified": True, "observed": {"path": path, "size": len(str(obs_content or ""))}, "expected_diff": None}
    if isinstance(expected, str):
        exp_bytes = expected.encode("utf-8")
    else:
        exp_bytes = expected
    if isinstance(obs_content, str):
        obs_bytes = obs_content.encode("utf-8")
    else:
        obs_bytes = obs_content or b""
    exp_hash = hashlib.sha256(exp_bytes).hexdigest()[:16]
    obs_hash = hashlib.sha256(obs_bytes).hexdigest()[:16]
    verified = exp_hash == obs_hash and len(exp_bytes) == len(obs_bytes)
    return {
        "verified": verified,
        "observed": {"path": path, "size": len(obs_bytes), "hash": obs_hash},
        "expected_diff": None if verified else {"expected_hash": exp_hash, "observed_hash": obs_hash, "expected_size": len(exp_bytes), "observed_size": len(obs_bytes)},
    }


async def _readback_file_edit(path: str, old_string: str = "", new_string: str = "", **kwargs: Any) -> dict[str, Any]:
    """Confirm new_string is present (and old_string absent if replace)."""
    ws = _get_ws(kwargs)
    if not getattr(ws, "_created", False):
        await ws.create()
    observed = await ws.read(path)
    if observed.get("error_code"):
        return {"verified": False, "observed": observed, "expected_diff": "file missing after edit"}
    content = str(observed.get("content") or "")
    has_new = new_string in content if new_string else True
    has_old = old_string in content if old_string else False
    verified = has_new and (not has_old if old_string and not kwargs.get("replace_all") else True)
    # For replace_all, old may still appear zero times
    if kwargs.get("replace_all") and old_string:
        verified = has_new and (old_string not in content)
    return {
        "verified": verified,
        "observed": {"path": path, "contains_new": has_new, "contains_old": has_old, "size": len(content)},
        "expected_diff": None if verified else {"missing_new": not has_new, "still_has_old": has_old},
    }


async def _readback_deliver_file(path: str, filename: str | None = None, **kwargs: Any) -> dict[str, Any]:
    """Stat the source file and confirm deliver payload referenced it."""
    import hashlib
    ws = _get_ws(kwargs)
    if not getattr(ws, "_created", False):
        await ws.create()
    observed = await ws.read(path, max_bytes=1024 * 1024)
    if observed.get("error_code"):
        return {"verified": False, "observed": observed, "expected_diff": "source file missing after deliver"}
    content = observed.get("content")
    if content is None and observed.get("content_b64"):
        import base64
        content_bytes = base64.b64decode(observed["content_b64"])
    elif isinstance(content, str):
        content_bytes = content.encode("utf-8")
    else:
        content_bytes = b""
    h = hashlib.sha256(content_bytes).hexdigest()[:16]
    return {
        "verified": True,
        "observed": {"path": path, "size": len(content_bytes), "hash": h, "filename": filename or path.split("/")[-1]},
        "expected_diff": None,
    }


async def _readback_generic_get(get_tool_name: str, id_arg: str, args: dict, registry_execute=None) -> dict[str, Any]:
    """Generic readback: call a get_* tool with the id produced by a create/send."""
    # Prefer explicit id fields from the write result if present in args
    lookup = {}
    for key in (id_arg, "id", "draft_id", "event_id", "message_id", "thread_id"):
        if key in args and args[key]:
            lookup[id_arg] = args[key]
            break
    if not lookup and "result_id" in args:
        lookup[id_arg] = args["result_id"]
    if not lookup:
        return {"verified": False, "observed": {}, "expected_diff": f"no {id_arg} available for readback"}
    
    # Actually execute the get tool if registry_execute is provided
    if registry_execute is not None:
        try:
            observed = await registry_execute(**{id_arg: lookup[id_arg]})
            ok = not (isinstance(observed, dict) and observed.get("error_code"))
            return {
                "verified": ok,
                "observed": observed if isinstance(observed, dict) else {"raw": str(observed)[:500]},
                "expected_diff": None if ok else "get after write returned error",
            }
        except Exception as exc:
            return {
                "verified": False,
                "observed": {},
                "expected_diff": f"readback exception: {exc}",
            }
    
    return {"verified": None, "observed": {"would_call": get_tool_name, "args": lookup}, "expected_diff": "deferred_to_loop"}


# ---------------------------------------------------------------------------
# Enhanced Readback System: Registry-based verification strategies
# ---------------------------------------------------------------------------

class ReadbackStrategy:
    """Base class for readback verification strategies."""
    
    async def verify(self, tool_name: str, args: dict, result: dict, **kwargs) -> dict[str, Any]:
        """Verify a tool's mutation by re-observing the world."""
        raise NotImplementedError


class FileWriteReadback(ReadbackStrategy):
    """Verify file write by re-reading and comparing hash."""
    
    async def verify(self, tool_name: str, args: dict, result: dict, **kwargs) -> dict[str, Any]:
        return await _readback_file_write(args.get("path", ""), args.get("content"), **kwargs)


class FileEditReadback(ReadbackStrategy):
    """Verify file edit by checking new content presence and old content absence."""
    
    async def verify(self, tool_name: str, args: dict, result: dict, **kwargs) -> dict[str, Any]:
        return await _readback_file_edit(
            args.get("path", ""),
            args.get("old_string", ""),
            args.get("new_string", ""),
            **kwargs
        )


class DeliverFileReadback(ReadbackStrategy):
    """Verify file delivery by checking source file existence and hash."""
    
    async def verify(self, tool_name: str, args: dict, result: dict, **kwargs) -> dict[str, Any]:
        return await _readback_deliver_file(args.get("path", ""), args.get("filename"), **kwargs)


class ShellExecReadback(ReadbackStrategy):
    """Verify shell command effects by checking exit code and output."""
    
    async def verify(self, tool_name: str, args: dict, result: dict, **kwargs) -> dict[str, Any]:
        # For shell_exec, verify the command completed successfully
        if isinstance(result, dict):
            exit_code = result.get("exit_code")
            if exit_code is not None:
                verified = exit_code == 0
                return {
                    "verified": verified,
                    "observed": {"exit_code": exit_code, "stdout": result.get("stdout", "")[:200]},
                    "expected_diff": None if verified else f"Command exited with code {exit_code}",
                }
        return {"verified": True, "observed": {"completed": True}, "expected_diff": None}


class PythonExecReadback(ReadbackStrategy):
    """Verify Python execution by checking for errors in output."""
    
    async def verify(self, tool_name: str, args: dict, result: dict, **kwargs) -> dict[str, Any]:
        if isinstance(result, dict):
            error = result.get("error") or result.get("stderr")
            verified = not error
            return {
                "verified": verified,
                "observed": {"stdout": result.get("stdout", "")[:200], "stderr": str(error)[:200] if error else ""},
                "expected_diff": None if verified else f"Python execution error: {error}",
            }
        return {"verified": True, "observed": {"completed": True}, "expected_diff": None}


class DeployWebsiteReadback(ReadbackStrategy):
    """Verify website deployment by checking deployment URL accessibility."""
    
    async def verify(self, tool_name: str, args: dict, result: dict, **kwargs) -> dict[str, Any]:
        if isinstance(result, dict) and result.get("ok"):
            deploy_url = result.get("deploy_url", "")
            return {
                "verified": bool(deploy_url and deploy_url.startswith("http")),
                "observed": {"deploy_url": deploy_url, "provider": result.get("provider")},
                "expected_diff": None if deploy_url else "No deployment URL returned",
            }
        return {"verified": False, "observed": result, "expected_diff": "Deployment failed or returned no URL"}


class PublishWebsiteReadback(ReadbackStrategy):
    """Verify website publish by checking webhook response."""
    
    async def verify(self, tool_name: str, args: dict, result: dict, **kwargs) -> dict[str, Any]:
        if isinstance(result, dict) and result.get("published"):
            return {
                "verified": True,
                "observed": {"target": result.get("target"), "project_name": result.get("project_name")},
                "expected_diff": None,
            }
        return {"verified": False, "observed": result, "expected_diff": "Publish failed or not confirmed"}


class CreateWebsiteReadback(ReadbackStrategy):
    """Verify website creation by checking expected files exist."""
    
    async def verify(self, tool_name: str, args: dict, result: dict, **kwargs) -> dict[str, Any]:
        if isinstance(result, dict) and result.get("ok"):
            files_created = result.get("files_created", [])
            # Check for essential files
            has_index = any("index.html" in f for f in files_created)
            has_readme = any("README" in f.upper() for f in files_created)
            return {
                "verified": has_index,
                "observed": {"files_created": len(files_created), "has_index": has_index, "has_readme": has_readme},
                "expected_diff": None if has_index else "No index.html created",
            }
        return {"verified": False, "observed": result, "expected_diff": "Website creation failed"}


class ScreenshotReadback(ReadbackStrategy):
    """Verify screenshot capture by checking image data presence."""
    
    async def verify(self, tool_name: str, args: dict, result: dict, **kwargs) -> dict[str, Any]:
        if isinstance(result, dict) and result.get("ok"):
            screenshots = result.get("screenshots", {})
            verified = len(screenshots) > 0
            return {
                "verified": verified,
                "observed": {"viewports": list(screenshots.keys()), "viewport_count": len(screenshots)},
                "expected_diff": None if verified else "No screenshots captured",
            }
        return {"verified": False, "observed": result, "expected_diff": "Screenshot capture failed"}


class PreviewWebsiteReadback(ReadbackStrategy):
    """Verify website preview by checking quality checks passed."""
    
    async def verify(self, tool_name: str, args: dict, result: dict, **kwargs) -> dict[str, Any]:
        if isinstance(result, dict) and result.get("ok"):
            quality = result.get("quality_check", {})
            issues = quality.get("issues_to_fix", [])
            verified = quality.get("ok", False) and len(issues) == 0
            return {
                "verified": verified,
                "observed": {"preview_id": result.get("preview_id"), "issues_count": len(issues)},
                "expected_diff": None if verified else f"Preview has {len(issues)} issues: {issues[:3]}",
            }
        return {"verified": False, "observed": result, "expected_diff": "Preview creation failed"}


# Registry of readback strategies
_READBACK_STRATEGIES: dict[str, ReadbackStrategy] = {
    "file_write": FileWriteReadback(),
    "file_edit": FileEditReadback(),
    "deliver_file": DeliverFileReadback(),
    "shell_exec": ShellExecReadback(),
    "python_exec": PythonExecReadback(),
    "deploy_website": DeployWebsiteReadback(),
    "publish_website": PublishWebsiteReadback(),
    "create_website": CreateWebsiteReadback(),
    "screenshot_website": ScreenshotReadback(),
    "preview_website": PreviewWebsiteReadback(),
}


def get_readback_strategy(tool_name: str) -> ReadbackStrategy | None:
    """Get the readback strategy for a tool."""
    return _READBACK_STRATEGIES.get(tool_name)


async def execute_readback(tool_name: str, args: dict, result: dict, **kwargs) -> dict[str, Any]:
    """Execute the appropriate readback strategy for a tool."""
    strategy = get_readback_strategy(tool_name)
    if strategy is None:
        return {
            "verified": None,
            "observed": {},
            "expected_diff": f"No readback strategy for {tool_name}",
        }
    try:
        return await strategy.verify(tool_name, args, result, **kwargs)
    except Exception as exc:
        return {
            "verified": False,
            "observed": {},
            "expected_diff": f"Readback strategy error: {exc}",
        }


def register_readback_strategy(tool_name: str, strategy: ReadbackStrategy) -> None:
    """Register a custom readback strategy for a tool."""
    _READBACK_STRATEGIES[tool_name] = strategy




async def render_preview_tool(path: str, pages: list | None = None, **kwargs: Any) -> dict[str, Any]:
    """Phase-4 deliverable quality: PNG previews + lint."""
    from app.runtime.deliverable import render_preview
    root = kwargs.get("workspace_root") or os.environ.get("ONSITE48_WORKSPACE_ROOT") or "/tmp/onsite48_workspaces"
    # Prefer the active workspace root if provided via closure
    return render_preview(path, root, pages=pages)


async def screenshot_website(
    path: str = ".",
    viewports: list[dict] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Capture screenshots of a website at specified viewports for visual verification.

    Returns base64-encoded PNG screenshots that can be reviewed by the model.
    Use after preview_website to visually verify the rendered output.
    """
    import asyncio
    import tempfile
    from pathlib import Path
    from app.runtime.website_build import (
        export_project_files,
        materialize_site,
    )

    ws = _get_ws(kwargs)
    if not getattr(ws, "_created", False):
        await ws.create()

    try:
        files = await export_project_files(ws, path)
    except (ValueError, OSError) as exc:
        return {"error_code": "website_screenshot_failed", "message": str(exc)[:500], "retryable": False}

    # Default viewports: desktop and mobile
    if viewports is None:
        viewports = [
            {"name": "desktop", "width": 1440, "height": 900},
            {"name": "mobile", "width": 390, "height": 844},
        ]

    with tempfile.TemporaryDirectory(prefix="onsite48-screenshot-") as temp_dir:
        site_root = Path(temp_dir)
        materialize_site(files, site_root)

        # Use the existing browser smoke check logic but return screenshots
        async def _capture() -> dict[str, Any]:
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                return {"error_code": "playwright_not_installed", "message": "Playwright is not installed", "retryable": False}

            class QuietHandler(http.server.SimpleHTTPRequestHandler):
                def log_message(self, *_args: Any) -> None:
                    return

            handler = lambda *args, **kw: QuietHandler(*args, directory=str(site_root), **kw)
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            try:
                async with async_playwright() as playwright:
                    try:
                        browser = await playwright.chromium.launch(headless=True)
                    except Exception as exc:
                        return {"error_code": "chromium_unavailable", "message": f"Chromium is unavailable: {str(exc)[:180]}", "retryable": False}

                    screenshots = {}
                    viewports_checked = []
                    all_issues = []

                    url = f"http://127.0.0.1:{server.server_port}/index.html"

                    for vp in viewports:
                        page = await browser.new_page(viewport={"width": vp["width"], "height": vp["height"]})
                        console_errors: list[str] = []
                        page_errors: list[str] = []
                        failed_requests: list[str] = []
                        page.on("console", lambda msg: console_errors.append(msg.text[:200]) if msg.type == "error" else None)
                        page.on("pageerror", lambda err: page_errors.append(str(err)[:200]))
                        page.on("requestfailed", lambda req: failed_requests.append(req.url[:200]))

                        try:
                            response = await page.goto(url, wait_until="networkidle", timeout=12_000)
                            http_status = response.status if response else None
                            title = await page.title()

                            # Capture full-page screenshot
                            screenshot_bytes = await page.screenshot(full_page=True, type="png")
                            import base64
                            screenshot_b64 = base64.b64encode(screenshot_bytes).decode("ascii")

                            screenshots[vp["name"]] = screenshot_b64
                            viewports_checked.append({
                                "name": vp["name"],
                                "width": vp["width"],
                                "height": vp["height"],
                                "http_status": http_status,
                                "title": title[:200],
                                "console_errors": console_errors[:10],
                                "page_errors": page_errors[:10],
                                "failed_requests": failed_requests[:10],
                            })

                            if console_errors:
                                all_issues.extend([f"console ({vp['name']}): {m}" for m in console_errors[:3]])
                            if page_errors:
                                all_issues.extend([f"page error ({vp['name']}): {m}" for m in page_errors[:3]])
                            if failed_requests:
                                all_issues.extend([f"failed request ({vp['name']}): {u}" for u in failed_requests[:3]])

                        except Exception as exc:
                            all_issues.append(f"error ({vp['name']}): {str(exc)[:200]}")
                        finally:
                            await page.close()

                    await browser.close()
                    return {
                        "ok": not all_issues,
                        "screenshots": screenshots,
                        "viewports_checked": viewports_checked,
                        "issues": all_issues,
                    }
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        return await _capture()


async def deploy_website(
    path: str = ".",
    provider: str = "netlify",
    project_name: str = "website",
    **kwargs: Any,
) -> dict[str, Any]:
    """Deploy a static website to a real hosting provider (Netlify, Vercel, Cloudflare Pages, etc.).

    This creates an actual live deployment, unlike publish_website which only sends to a webhook.
    Requires the provider's CLI tool to be installed and authenticated in the workspace.
    
    Providers:
    - "netlify": Uses `netlify deploy` (requires NETLIFY_AUTH_TOKEN)
    - "vercel": Uses `vercel deploy` (requires VERCEL_TOKEN)
    - "cloudflare": Uses `wrangler pages deploy` (requires CLOUDFLARE_API_TOKEN)
    - "firebase": Uses `firebase deploy --only hosting` (requires FIREBASE_TOKEN)
    - "surge": Uses `surge` (requires SURGE_LOGIN, SURGE_TOKEN)
    
    The tool runs the provider's CLI in the workspace, so authentication must be
    configured via environment variables or prior login in the workspace.
    """
    import asyncio
    import os
    import shutil
    import tempfile
    from pathlib import Path
    from app.runtime.website_build import export_project_files, materialize_site

    ws = _get_ws(kwargs)
    if not getattr(ws, "_created", False):
        await ws.create()

    try:
        files = await export_project_files(ws, path)
    except (ValueError, OSError) as exc:
        return {"error_code": "website_deploy_failed", "message": str(exc)[:500], "retryable": False}

    if not any(name in files for name in ("index.html",)):
        return {"error_code": "website_project_empty", "message": "No index.html found in project", "retryable": False}

    # Materialize to temp directory for deployment
    with tempfile.TemporaryDirectory(prefix="onsite48-deploy-") as temp_dir:
        site_root = Path(temp_dir)
        materialize_site(files, site_root)

        # Check if provider CLI is available
        provider_lower = provider.lower()
        cli_commands = {
            "netlify": ["netlify", "deploy", "--dir", str(site_root), "--prod", "--json"],
            "vercel": ["vercel", "deploy", str(site_root), "--prod", "--token", os.getenv("VERCEL_TOKEN", "")],
            "cloudflare": ["wrangler", "pages", "deploy", str(site_root), "--project-name", project_name],
            "firebase": ["firebase", "deploy", "--only", "hosting", "--project", project_name, "--token", os.getenv("FIREBASE_TOKEN", "")],
            "surge": ["surge", str(site_root), "--domain", f"{project_name}.surge.sh"],
        }

        if provider_lower not in cli_commands:
            return {"error_code": "unknown_provider", "message": f"Unknown provider: {provider}. Available: {', '.join(cli_commands.keys())}", "retryable": False}

        cmd = cli_commands[provider_lower]
        # Filter out empty tokens
        cmd = [c for c in cmd if c]

        # Check if CLI is installed
        cli_name = cmd[0]
        if not shutil.which(cli_name):
            return {"error_code": "cli_not_installed", "message": f"{cli_name} CLI not found in PATH. Install it in the workspace first.", "retryable": False}

        # Run deployment
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(site_root),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

            if proc.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace")[:500]
                return {"error_code": "deployment_failed", "message": f"{provider} deployment failed: {error_msg}", "retryable": True}

            # Parse output for deployment URL
            output = stdout.decode("utf-8", errors="replace")
            
            # Try to extract URL from output
            import re
            url_match = re.search(r"https?://[^\s]+\.(netlify\.app|vercel\.app|cloudflare\.com|pages\.dev|firebaseapp\.com|surge\.sh)", output)
            deploy_url = url_match.group(0) if url_match else "See CLI output for URL"

            return {
                "ok": True,
                "provider": provider_lower,
                "project_name": project_name,
                "deploy_url": deploy_url,
                "raw_output": output[:2000],
                "message": f"Deployed to {provider_lower}. URL: {deploy_url}",
            }

        except asyncio.TimeoutError:
            return {"error_code": "deployment_timeout", "message": "Deployment timed out after 120 seconds", "retryable": True}
        except Exception as exc:
            return {"error_code": "deployment_error", "message": f"Deployment error: {str(exc)[:300]}", "retryable": True}


def _builtin_workspace_tools(workspace_root: str | None = None) -> list[ToolSpec]:
    """Computer tools backed by runtime.workspace (never host-side for prod)."""
    import os

    async def _ensure(ws):
        if not getattr(ws, "_created", False):
            await ws.create()

    async def shell_exec(command: str, timeout: int = 60, cwd: str | None = None, **kwargs: Any) -> dict[str, Any]:
        ws = _get_ws(kwargs)
        await _ensure(ws)
        result = await ws.exec(command, timeout=int(timeout or 60), cwd=cwd)
        return result.to_dict()

    async def file_read(path: str, **kwargs: Any) -> dict[str, Any]:
        ws = _get_ws(kwargs)
        await _ensure(ws)
        return await ws.read(path)

    async def file_write(path: str, content: str, **kwargs: Any) -> dict[str, Any]:
        ws = _get_ws(kwargs)
        await _ensure(ws)
        return await ws.write(path, content)

    async def file_edit(
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        ws = _get_ws(kwargs)
        await _ensure(ws)
        return await ws.edit(path, old_string, new_string, replace_all=bool(replace_all))

    async def file_list(path: str = ".", recursive: bool = False, **kwargs: Any) -> dict[str, Any]:
        ws = _get_ws(kwargs)
        await _ensure(ws)
        return await ws.list(path, recursive=bool(recursive))

    async def file_grep(pattern: str, path: str = ".", max_matches: int = 50, **kwargs: Any) -> dict[str, Any]:
        ws = _get_ws(kwargs)
        await _ensure(ws)
        return await ws.grep(pattern, path, max_matches=int(max_matches or 50))

    async def python_exec(code: str, timeout: int = 60, **kwargs: Any) -> dict[str, Any]:
        ws = _get_ws(kwargs)
        await _ensure(ws)
        return await ws.python_exec(code, timeout=int(timeout or 60))

    async def deliver_file(path: str, filename: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """Publish a workspace file to the artifact/library store with lineage."""
        ws = _get_ws(kwargs)
        await _ensure(ws)
        data = await ws.read(path, max_bytes=20 * 1024 * 1024)
        if data.get("error_code"):
            return data
        content = data.get("content")
        if content is None and data.get("content_b64"):
            import base64
            content_bytes = base64.b64decode(data["content_b64"])
        elif isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            return {"error_code": "empty", "message": "no content", "retryable": False}
        try:
            from app.services.artifact_versions import artifact_versions
            org_id = kwargs.get("org_id") or kwargs.get("organization_id") or "default"
            project_id = kwargs.get("project_id")
            fname = filename or path.split("/")[-1]
            # naive content-type
            ctype = "application/octet-stream"
            if fname.endswith(".md"):
                ctype = "text/markdown"
            elif fname.endswith(".csv"):
                ctype = "text/csv"
            elif fname.endswith(".json"):
                ctype = "application/json"
            elif fname.endswith(".html") or fname.endswith(".htm"):
                ctype = "text/html"
            elif fname.endswith(".js") or fname.endswith(".mjs"):
                ctype = "application/javascript"
            elif fname.endswith(".css"):
                ctype = "text/css"
            elif fname.endswith((".png", ".jpg", ".jpeg")):
                ctype = "image/png" if fname.endswith(".png") else "image/jpeg"
            elif fname.endswith(".pdf"):
                ctype = "application/pdf"
            elif fname.endswith(".docx"):
                ctype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            elif fname.endswith(".xlsx"):
                ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            elif fname.endswith(".pptx"):
                ctype = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            item = artifact_versions.create_version(
                organization_id=str(org_id),
                project_id=project_id,
                artifact_id=None,
                filename=fname,
                content=content_bytes,
                content_type=ctype,
                preview={"source_path": path, "run_id": _run_id_from_kwargs(kwargs)},
            )
            return {"delivered": True, "artifact": item, "path": path}
        except Exception as e:
            # Fallback: copy to out/ and report path (artifact store may need storage backend)
            out_name = f"out/{filename or path.split('/')[-1]}"
            await ws.write(out_name, content_bytes)
            return {
                "delivered": False,
                "fallback_path": out_name,
                "message": f"artifact store unavailable: {e}",
                "retryable": True,
            }

    async def preview_website(path: str = ".", project_name: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """Snapshot, lint, browser-check, and expose a static project preview.

        When Playwright/Chromium is available, the browser smoke check is BLOCKING:
        the preview will fail if console errors, page errors, or failed requests are detected.
        This ensures only visually and functionally correct sites pass the preview gate.
        """
        import tempfile
        from pathlib import Path
        from app.runtime.website_build import (
            audit_static_site,
            browser_smoke_check,
            export_project_files,
            materialize_site,
        )
        from app.services.execution_identity import get_execution_identity
        from app.services.website_preview import create_preview, cleanup_expired_previews

        ws = _get_ws(kwargs)
        if not getattr(ws, "_created", False):
            await ws.create()
        try:
            identity = get_execution_identity()
        except RuntimeError:
            return {"error_code": "preview_identity_missing", "message": "Website previews require an authenticated task context.", "retryable": False}
        try:
            files = await export_project_files(ws, path)
            audit = audit_static_site(files)
            with tempfile.TemporaryDirectory(prefix="onsite48-site-check-") as temp_dir:
                site_root = Path(temp_dir)
                materialize_site(files, site_root)
                browser_check = await browser_smoke_check(site_root)
            cleanup_expired_previews()
            record = create_preview(
                files,
                user_id=str(identity.user_id),
                organization_id=str(identity.organization_id),
                thread_id=identity.task_thread_id,
                project_name=project_name or Path(path.rstrip("/") or "website").name,
            )
            blocking_issues = list(audit.get("issues") or [])
            
            # Make browser smoke check BLOCKING when Playwright is available
            browser_available = browser_check.get("available", False)
            if browser_available:
                blocking_issues.extend(browser_check.get("issues") or [])
                # If Playwright ran but found issues, the preview fails
                if browser_check.get("issues"):
                    return {
                        "ok": False,
                        "error_code": "browser_check_failed",
                        "message": "Browser smoke check failed. Fix the issues below before previewing.",
                        "quality_check": {
                            "ok": False,
                            "static_audit": audit,
                            "browser_smoke_check": browser_check,
                            "issues_to_fix": blocking_issues,
                        },
                        "retryable": True,
                    }
            
            return {
                "ok": True,
                **record,
                "quality_check": {
                    "ok": not blocking_issues,
                    "static_audit": audit,
                    "browser_smoke_check": browser_check,
                    "issues_to_fix": blocking_issues,
                },
                "message": "Preview is ready. Review it at desktop and mobile sizes, fix any listed issues, then package the project before finishing.",
            }
        except (ValueError, OSError) as exc:
            return {"error_code": "website_preview_failed", "message": str(exc)[:500], "retryable": False}
        except Exception as exc:
            logger.exception("website.preview_failed", path=path, error=str(exc))
            return {"error_code": "website_preview_failed", "message": f"Could not create website preview: {str(exc)[:300]}", "retryable": True}

    async def package_website(path: str = ".", project_name: str = "website", **kwargs: Any) -> dict[str, Any]:
        """Create a user-downloadable ZIP containing the complete project source."""
        import io
        import zipfile
        from pathlib import Path
        from app.runtime.website_build import export_project_files
        from app.services.execution_identity import get_execution_identity
        from app.services.artifact_contract import ArtifactDescriptor, signed_download_url
        from app.services.attachment_store import MAX_FILE_SIZE, attachment_store

        ws = _get_ws(kwargs)
        if not getattr(ws, "_created", False):
            await ws.create()
        try:
            identity = get_execution_identity()
            files = await export_project_files(ws, path)
        except (RuntimeError, ValueError, OSError) as exc:
            return {"error_code": "website_package_failed", "message": str(exc)[:500], "retryable": False}
        if not any(name in files for name in ("index.html", "package.json", "README.md", "readme.md")):
            return {"error_code": "website_project_empty", "message": "No recognizable website project files were found.", "retryable": False}
        if not any(Path(name).name.lower() in {"readme.md", "readme.txt"} for name in files):
            files["README.md"] = (
                f"# {project_name}\n\n"
                "Website project source packaged by Onsite48.\n\n"
                "For a static site, open `index.html` or serve this folder with a local HTTP server. "
                "If `package.json` is present, install its declared dependencies and use the scripts "
                "documented there. No public deployment was performed.\n"
            ).encode("utf-8")
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for name, content in sorted(files.items()):
                bundle.writestr(name, content)
        content = archive.getvalue()
        if len(content) > MAX_FILE_SIZE:
            return {
                "error_code": "website_project_too_large",
                "message": f"The source ZIP is {len(content) // (1024 * 1024)} MiB; downloadable project artifacts are limited to {MAX_FILE_SIZE // (1024 * 1024)} MiB.",
                "retryable": False,
            }
        try:
            stored = attachment_store.save_generated_bytes(
                organization_id=str(identity.organization_id),
                user_id=str(identity.user_id),
                filename=f"{project_name or 'website'}-source.zip",
                content=content,
                content_type="application/zip",
                artifact_type="generated_website_project",
                source_title=project_name or "Website project",
            )
            descriptor = ArtifactDescriptor(
                attachment_id=str(stored["id"]),
                name=str(stored["name"]),
                content_type="application/zip",
                size=int(stored["size"]),
                sha256=str(stored["sha256"]),
                artifact_type="generated_website_project",
                download_url=signed_download_url(str(stored["id"]), ttl_seconds=86_400),
                source_title=project_name or "Website project",
                provenance={"kind": "website_source", "thread_id": identity.task_thread_id},
                preview="Complete source project ZIP; public deployment was not performed.",
            )
            return {"ok": True, "artifact": descriptor.event_payload(), "files": len(files), "size_bytes": len(content)}
        except Exception as exc:
            logger.exception("website.package_delivery_failed", error=str(exc))
            return {"error_code": "website_package_delivery_failed", "message": str(exc)[:300], "retryable": True}

    async def publish_website(path: str, project_name: str = "website", target: str = "staging", **kwargs: Any) -> dict[str, Any]:
        """Send a project to an explicitly configured deployment webhook.

        The tool spec is an external-send operation and always pauses for the
        existing approval workflow before this function may execute.
        """
        import asyncio
        import base64
        import io
        import json
        import urllib.error
        import urllib.request
        import zipfile
        from urllib.parse import urlparse
        from app.runtime.website_build import export_project_files
        from app.services.execution_identity import get_execution_identity

        endpoint = os.getenv("WEBSITE_PUBLISH_WEBHOOK_URL", "").strip()
        token = os.getenv("WEBSITE_PUBLISH_WEBHOOK_TOKEN", "").strip()
        if not endpoint or not token:
            return {"error_code": "publisher_not_configured", "message": "No website publishing provider is configured. The site remains preview-only; configure WEBSITE_PUBLISH_WEBHOOK_URL and WEBSITE_PUBLISH_WEBHOOK_TOKEN to enable this approved action.", "retryable": False}
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" and not (os.getenv("SYNTHAI_ENV", "development").lower() not in {"production", "prod"} and parsed.hostname in {"localhost", "127.0.0.1"}):
            return {"error_code": "invalid_publish_endpoint", "message": "The configured publishing endpoint must use HTTPS.", "retryable": False}
        if target not in {"staging", "production"}:
            return {"error_code": "invalid_publish_target", "message": "target must be staging or production.", "retryable": False}
        ws = _get_ws(kwargs)
        if not getattr(ws, "_created", False):
            await ws.create()
        try:
            identity = get_execution_identity()
            files = await export_project_files(ws, path)
        except (RuntimeError, ValueError, OSError) as exc:
            return {"error_code": "website_publish_package_failed", "message": str(exc)[:400], "retryable": False}
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for name, content in sorted(files.items()):
                bundle.writestr(name, content)
        payload = json.dumps({
            "project_name": project_name,
            "target": target,
            "thread_id": identity.task_thread_id,
            "organization_id": str(identity.organization_id),
            "source_zip_base64": base64.b64encode(archive.getvalue()).decode("ascii"),
        }).encode("utf-8")
        request = urllib.request.Request(endpoint, data=payload, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST")

        def send_webhook() -> dict[str, Any]:
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    result = json.loads(response.read(64 * 1024).decode("utf-8", errors="replace") or "{}")
                    if response.status < 200 or response.status >= 300:
                        return {"error_code": "website_publish_failed", "message": f"Publisher returned HTTP {response.status}.", "retryable": response.status >= 500}
                    return {"published": True, "target": target, "project_name": project_name, "result": result}
            except urllib.error.HTTPError as exc:
                return {"error_code": "website_publish_failed", "message": f"Publisher returned HTTP {exc.code}.", "retryable": exc.code >= 500}
            except Exception as exc:
                return {"error_code": "website_publish_failed", "message": f"Publishing failed: {str(exc)[:220]}", "retryable": True}

        return await asyncio.to_thread(send_webhook)

    async def load_skill(name: str, **kwargs: Any) -> dict[str, Any]:
        """Load a skill instruction package (progressive disclosure)."""
        from app.skills.loader import load_skill_body
        run_id = _run_id_from_kwargs(kwargs)
        body = load_skill_body(name, org_id=kwargs.get("org_id"), user_id=kwargs.get("user_id"))
        if body.get("error_code"):
            return body
        # Journal load if journal available
        try:
            from app.runtime.journal import journal as default_journal
            await default_journal.append(
                run_id,
                "skill_loaded",
                {"name": name, "source": body.get("source")},
            )
        except Exception:
            pass
        return body

    async def create_website(
        template: str = "basic",
        project_name: str = "My Website",
        project_description: str = "A modern website",
        destination: str = ".",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Scaffold a new website project from a template.

        Templates:
        - "basic": Vanilla HTML/CSS/JS (no build step)
        - "react": React + Vite (modern SPA)

        Creates a complete project structure with index.html, styles, scripts,
        and README. Use preview_website to verify, then package_website to deliver.
        """
        import shutil
        from pathlib import Path, PurePosixPath

        ws = _get_ws(kwargs)
        if not getattr(ws, "_created", False):
            await ws.create()

        # Validate destination
        dest_text = (destination or ".").strip() or "."
        pure = PurePosixPath(dest_text.replace("\\", "/"))
        if pure.is_absolute() or any(part == ".." for part in pure.parts):
            return {"error_code": "invalid_path", "message": "destination must be workspace-relative and may not contain '..'", "retryable": False}

        # Template mapping
        templates = {
            "basic": "basic",
            "react": "react",
        }
        template_dir = templates.get(template.lower())
        if not template_dir:
            return {"error_code": "unknown_template", "message": f"Unknown template: {template}. Available: {', '.join(templates.keys())}", "retryable": False}

        template_root = Path(__file__).parent.parent.parent / "templates" / "website" / template_dir
        if not template_root.exists():
            return {"error_code": "template_missing", "message": f"Template files not found for {template}", "retryable": False}

        # Generate slug from project_name
        import re
        slug = re.sub(r'[^a-z0-9]+', '-', project_name.lower()).strip('-') or "website"
        current_year = str(__import__("datetime").datetime.now().year)

        # Color palette defaults
        primary_color = "#2563eb"
        primary_color_dark = "#1d4ed8"
        primary_color_light = "#dbeafe"
        secondary_color = "#0891b2"
        secondary_color_dark = "#0e7490"

        # Hero/content defaults
        hero_title = f"Welcome to {project_name}"
        hero_subtitle = project_description
        cta_primary = "Get Started"
        cta_secondary = "Learn More"
        about_text = f"{project_name} is a modern web project built with care. {project_description}"
        features = [
            {"icon": "⚡", "title": "Fast", "description": "Optimized for performance"},
            {"icon": "🎨", "title": "Beautiful", "description": "Clean, modern design"},
            {"icon": "📱", "title": "Responsive", "description": "Works on all devices"},
        ]

        created_files = []
        errors = []

        try:
            for src_path in template_root.rglob("*"):
                if not src_path.is_file():
                    continue

                # Get relative path from template root
                rel = src_path.relative_to(template_root)
                dest_path = Path(dest_text) / rel

                # Read template content
                content = src_path.read_text(encoding="utf-8")

                # Replace placeholders
                replacements = {
                    "{{PROJECT_NAME}}": project_name,
                    "{{PROJECT_NAME_SLUG}}": slug,
                    "{{PROJECT_DESCRIPTION}}": project_description,
                    "{{PRIMARY_COLOR}}": primary_color,
                    "{{PRIMARY_COLOR_DARK}}": primary_color_dark,
                    "{{PRIMARY_COLOR_LIGHT}}": primary_color_light,
                    "{{SECONDARY_COLOR}}": secondary_color,
                    "{{SECONDARY_COLOR_DARK}}": secondary_color_dark,
                    "{{HERO_TITLE}}": hero_title,
                    "{{HERO_SUBTITLE}}": hero_subtitle,
                    "{{CTA_PRIMARY}}": cta_primary,
                    "{{CTA_SECONDARY}}": cta_secondary,
                    "{{ABOUT_TEXT}}": about_text,
                    "{{CURRENT_YEAR}}": current_year,
                    "{{FEATURES}}": "",
                }

                # Handle features array for different templates
                if template_dir == "basic":
                    features_html = ""
                    for feat in features:
                        features_html += f'''          <article class="feature-card">
            <div class="feature-icon" aria-hidden="true">{feat["icon"]}</div>
            <h3>{feat["title"]}</h3>
            <p>{feat["description"]}</p>
          </article>
'''
                    replacements["{{FEATURES}}"] = features_html
                elif template_dir == "react":
                    # For React, features are in the component - we'll skip array replacement
                    pass

                for placeholder, value in replacements.items():
                    content = content.replace(placeholder, value)

                # Write to workspace
                try:
                    await ws.write(str(dest_path), content)
                    created_files.append(str(dest_path))
                except Exception as exc:
                    errors.append(f"{dest_path}: {exc}")

        except Exception as exc:
            return {"error_code": "creation_failed", "message": f"Failed to create website: {exc}", "retryable": True}

        if errors:
            return {"error_code": "partial_failure", "message": f"Created with errors: {'; '.join(errors)}", "files_created": created_files, "retryable": False}

        return {
            "ok": True,
            "template": template_dir,
            "project_name": project_name,
            "destination": dest_text,
            "files_created": created_files,
            "message": f"Created {template_dir} website project '{project_name}' at {dest_text}. Run preview_website to verify, then package_website to deliver.",
        }

    return [
        ToolSpec(
            name="shell_exec",
            description="Run a shell command inside the isolated task workspace. Use for installs, scripts, conversions.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "description": "Seconds"},
                    "cwd": {"type": "string", "description": "Relative cwd inside workspace"},
                    "mutating": {"type": "boolean", "description": "Whether the command mutates state (writes files, installs packages, etc.). Default false for read-only commands like ls, cat, grep.", "default": False},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            side_effect="write",
            idempotency="non_idempotent",
            timeout_s=90,
            max_output_chars=12000,
            trust="untrusted",
            category="system",
            _execute=shell_exec,
        ),
        ToolSpec(
            name="file_read",
            description="Read a file from the task workspace.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
            side_effect="read",
            idempotency="idempotent",
            timeout_s=15,
            max_output_chars=20000,
            trust="untrusted",
            category="filesystem",
            _execute=file_read,
        ),
        ToolSpec(
            name="file_write",
            description="Write a file in the task workspace.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False},
            side_effect="write",
            idempotency="non_idempotent",
            timeout_s=15,
            max_output_chars=2000,
            trust="trusted",
            category="filesystem",
            readback=_readback_file_write,
            _execute=file_write,
        ),
        ToolSpec(
            name="file_edit",
            description="Replace text in an existing workspace file.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["path", "old_string", "new_string"],
                "additionalProperties": False,
            },
            side_effect="write",
            idempotency="non_idempotent",
            timeout_s=15,
            max_output_chars=2000,
            trust="trusted",
            category="filesystem",
            readback=_readback_file_edit,
            _execute=file_edit,
        ),
        ToolSpec(
            name="file_list",
            description="List files and directories in the task workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "recursive": {"type": "boolean"},
                },
                "required": [],
                "additionalProperties": False,
            },
            side_effect="read",
            idempotency="idempotent",
            timeout_s=15,
            max_output_chars=12000,
            trust="untrusted",
            category="filesystem",
            _execute=file_list,
        ),
        ToolSpec(
            name="file_grep",
            description="Search for a regex pattern across files in the task workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "max_matches": {"type": "integer"},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
            side_effect="read",
            idempotency="idempotent",
            timeout_s=30,
            max_output_chars=12000,
            trust="untrusted",
            category="filesystem",
            _execute=file_grep,
        ),
        ToolSpec(
            name="python_exec",
            description="Execute Python code inside the isolated task workspace (full interpreter, packages available).",
            parameters={
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "timeout": {"type": "integer"},
                },
                "required": ["code"],
                "additionalProperties": False,
            },
            side_effect="write",
            idempotency="non_idempotent",
            timeout_s=90,
            max_output_chars=12000,
            trust="untrusted",
            category="code",
            _execute=python_exec,
        ),
        ToolSpec(
            name="deliver_file",
            description="Publish a workspace file as a user-facing artifact with version lineage.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "filename": {"type": "string"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            side_effect="write",
            idempotency="non_idempotent",
            timeout_s=30,
            max_output_chars=4000,
            trust="trusted",
            category="filesystem",
            readback=_readback_deliver_file,
            _execute=deliver_file,
        ),
        ToolSpec(
            name="render_preview",
            description=(
                "Convert a workspace deliverable (docx/pptx/xlsx/pdf/html) to PNG page previews "
                "and return deterministic lint (slide count, empty placeholders, broken links). "
                "Inspect previews before deliver_file. Use for visual quality checks."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative or absolute path to the deliverable"},
                    "pages": {"type": "array", "items": {"type": "integer"}, "description": "Optional page numbers to render"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            side_effect="read",
            idempotency="idempotent",
            timeout_s=120,
            max_output_chars=20000,
            trust="trusted",
            category="visualization",
            _execute=render_preview_tool,
        ),
        ToolSpec(
            name="preview_website",
            description=(
                "Create a short-lived interactive preview of a generated static website, "
                "check local assets and responsive HTML, and run desktop/mobile browser smoke checks. "
                "Use the built output directory containing index.html, inspect issues, and fix them."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative directory containing the static site's index.html"},
                    "project_name": {"type": "string"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            side_effect="write",
            idempotency="non_idempotent",
            timeout_s=120,
            max_output_chars=8000,
            trust="trusted",
            category="deployment",
            _execute=preview_website,
        ),
        ToolSpec(
            name="package_website",
            description=(
                "Package the complete website source project into a user-downloadable ZIP artifact. "
                "Use after build and preview checks; this never publishes the site publicly."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative project directory"},
                    "project_name": {"type": "string"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            side_effect="write",
            idempotency="non_idempotent",
            timeout_s=90,
            max_output_chars=4000,
            trust="trusted",
            category="deployment",
            _execute=package_website,
        ),
        ToolSpec(
            name="publish_website",
            description=(
                "Publish a website source ZIP to the configured deployment provider. "
                "This action is separate from previewing, requires explicit user approval, "
                "requires an explicit staging or production target, and will fail safely when no publisher is configured."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative website project directory"},
                    "project_name": {"type": "string"},
                    "target": {"type": "string", "enum": ["staging", "production"]},
                },
                "required": ["path", "target"],
                "additionalProperties": False,
            },
            side_effect="external_send",
            idempotency="non_idempotent",
            timeout_s=60,
            max_output_chars=4000,
            trust="trusted",
            category="deployment",
            _approval_required=True,
            _execute=publish_website,
        ),
        ToolSpec(
            name="load_skill",
            description="Load the full instructions for a skill by name. Use when a skill's description matches the task.",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
            side_effect="read",
            idempotency="idempotent",
            timeout_s=10,
            max_output_chars=30000,
            trust="trusted",
            category="skill",
            _execute=load_skill,
        ),
        ToolSpec(
            name="spawn_agent",
            description=(
                "Delegate a scoped sub-task to a specialist worker with isolated context. "
                "Roles: researcher, browser_operator, coder, writer, critic. "
                "Worker never sees lead history. Returns a handle; use await_agents to collect results. "
                "Prefer wide_map when processing many similar items in parallel."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": ["researcher", "browser_operator", "coder", "writer", "critic"]},
                    "goal": {"type": "string"},
                    "inputs": {"type": "array", "items": {}, "description": "Text or file_ref objects"},
                    "output_schema": {"type": "object"},
                    "budget": {"type": "object", "description": "Optional steps/tokens/cost_usd/wall_s overrides"},
                },
                "required": ["role", "goal"],
                "additionalProperties": False,
            },
            side_effect="read",
            idempotency="non_idempotent",
            timeout_s=30,
            max_output_chars=8000,
            trust="trusted",
            category="spawn",
            _execute=None,  # bound at runtime by loop
        ),
        ToolSpec(
            name="spawn_agents",
            description="Spawn multiple workers in parallel. Each item: {role, goal, inputs?, output_schema?, budget?}",
            parameters={
                "type": "object",
                "properties": {
                    "specs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {"type": "string"},
                                "goal": {"type": "string"},
                                "inputs": {"type": "array"},
                                "output_schema": {"type": "object"},
                                "budget": {"type": "object"},
                            },
                            "required": ["role", "goal"],
                        },
                    }
                },
                "required": ["specs"],
                "additionalProperties": False,
            },
            side_effect="read",
            idempotency="non_idempotent",
            timeout_s=60,
            max_output_chars=12000,
            trust="trusted",
            category="spawn",
            _execute=None,
        ),
        ToolSpec(
            name="await_agents",
            description="Wait for spawned worker handles to complete. Returns their status, summaries, artifacts, costs.",
            parameters={
                "type": "object",
                "properties": {
                    "worker_ids": {"type": "array", "items": {"type": "string"}, "description": "Optional subset; default all"},
                    "timeout_s": {"type": "number"},
                },
                "additionalProperties": False,
            },
            side_effect="read",
            idempotency="idempotent",
            timeout_s=600,
            max_output_chars=20000,
            trust="trusted",
            category="spawn",
            _execute=None,
        ),
        ToolSpec(
            name="wide_map",
            description=(
                "Process N items in parallel with isolated workers (same role + instruction template). "
                "Returns a table file (JSONL+CSV), short summary, and failed items. "
                "Partial failure never poisons the whole table. Cost preflight may request approval. "
                "Optional reduce_role runs a writer/critic over the table by reference."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": {}},
                    "role": {"type": "string", "enum": ["researcher", "browser_operator", "coder", "writer", "critic"]},
                    "instruction_template": {
                        "type": "string",
                        "description": "Template with {item} and {index} placeholders",
                    },
                    "output_schema": {"type": "object"},
                    "concurrency": {"type": "integer"},
                    "per_item_budget": {"type": "object"},
                    "reduce_role": {"type": "string"},
                    "reduce_instruction": {"type": "string"},
                },
                "required": ["items", "role", "instruction_template"],
                "additionalProperties": False,
            },
            side_effect="read",
            idempotency="non_idempotent",
            timeout_s=900,
            max_output_chars=20000,
            trust="trusted",
            category="spawn",
            _execute=None,
        ),
        ToolSpec(
            name="create_website",
            description=(
                "Scaffold a new website project from a template. Templates: 'basic' (vanilla HTML/CSS/JS), "
                "'react' (React + Vite). Creates complete project structure with index.html, styles, scripts, "
                "and README. Use preview_website to verify, then package_website to deliver."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "template": {"type": "string", "enum": ["basic", "react"], "description": "Template to use", "default": "basic"},
                    "project_name": {"type": "string", "description": "Display name for the project", "default": "My Website"},
                    "project_description": {"type": "string", "description": "Brief description for meta tags and content", "default": "A modern website"},
                    "destination": {"type": "string", "description": "Workspace-relative directory to create the project in", "default": "."},
                },
                "required": [],
                "additionalProperties": False,
            },
            side_effect="write",
            idempotency="non_idempotent",
            timeout_s=60,
            max_output_chars=4000,
            trust="trusted",
            category="deployment",
            _execute=create_website,
        ),
        ToolSpec(
            name="screenshot_website",
            description=(
                "Capture full-page screenshots of a website at desktop and mobile viewports "
                "for visual verification. Returns base64-encoded PNG images the model can review. "
                "Use after preview_website to visually verify the rendered output before finishing."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative directory containing the static site", "default": "."},
                    "viewports": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "width": {"type": "integer"},
                                "height": {"type": "integer"},
                            },
                            "required": ["name", "width", "height"],
                        },
                        "description": "Custom viewports (default: desktop 1440x900, mobile 390x844)",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            side_effect="read",
            idempotency="idempotent",
            timeout_s=120,
            max_output_chars=50000,
            trust="trusted",
            category="visualization",
            _execute=screenshot_website,
        ),
        ToolSpec(
            name="deploy_website",
            description=(
                "Deploy a static website to a real hosting provider (Netlify, Vercel, Cloudflare Pages, Firebase, Surge). "
                "Creates an actual live deployment using the provider's CLI. Requires the CLI to be installed and "
                "authenticated in the workspace (via environment variables). Use after preview_website passes."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative directory containing the static site", "default": "."},
                    "provider": {"type": "string", "enum": ["netlify", "vercel", "cloudflare", "firebase", "surge"], "description": "Hosting provider", "default": "netlify"},
                    "project_name": {"type": "string", "description": "Project name for the deployment", "default": "website"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            side_effect="external_send",
            idempotency="non_idempotent",
            timeout_s=180,
            max_output_chars=8000,
            trust="trusted",
            category="deployment",
            _approval_required=True,
            _execute=deploy_website,
        ),
    ]


def build_tool_specs(
    registry: ToolRegistry | None = None,
    *,
    workspace_root: str | None = None,
    include_registry: bool = True,
) -> dict[str, ToolSpec]:
    import os  # noqa: F401 — used by helpers
    specs: dict[str, ToolSpec] = {}
    for builtin in (_builtin_finish(), _builtin_ask_user(), *_builtin_workspace_tools(workspace_root)):
        specs[builtin.name] = builtin

    # Legacy tool gate (CTO patch v2): tools on this list are deprecated but
    # not deleted. Set LEGACY_TOOLS_DISABLED=1 (default) to remove them from
    # the model-visible surface. Names are exact tool names as registered.
    _disabled = os.environ.get("LEGACY_TOOLS_DISABLED", "1") == "1"
    _legacy_blocklist = {
        "browser_control",       # superseded by collapsed bridge surface
        "browser_bridge_raw",    # internal command escape hatch, never model-facing
        # The Tavily search tool is historically named "browser" (see its
        # docstring). Keep it enabled for now — planner prompts reference it —
        # but rename in a follow-up and then add "browser" here.
    }
    if include_registry and registry is not None:
        for name in registry.list_tools():
            if _disabled and name in _legacy_blocklist:
                logger.info("runtime.tools.legacy_tool_gated", name=name)
                continue
            tool = registry.get(name)
            if tool is None or name in specs:
                continue
            try:
                specs[name] = wrap_tool(tool)
            except Exception as exc:
                logger.warning("runtime.tools.wrap_failed", name=name, error=str(exc))
                specs[name] = ToolSpec(
                    name=name,
                    description=getattr(getattr(tool, "schema", None), "description", None) or f"Tool {name}",
                    parameters={"type": "object", "properties": {}, "additionalProperties": True},
                    side_effect="write",
                    idempotency="non_idempotent",
                    timeout_s=60,
                    max_output_chars=12000,
                    trust="untrusted",
                    _execute=(lambda t=tool: (lambda **kw: t.execute(**kw)))(),
                    _approval_required=True,
                )
    return specs

def specs_for_model(specs: dict[str, ToolSpec]) -> list[dict[str, Any]]:
    return [specs[k].to_openai_tool() for k in sorted(specs.keys())]
