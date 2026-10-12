from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import structlog

from app.runtime.context import ContextBuilder
from app.runtime.journal import Journal, journal as default_journal
from app.runtime.models import complete as model_complete
from app.runtime.policy import (
    Decision,
    PolicyDecisionResult,
    evaluate as policy_evaluate,
    org_policy_from_schedule,
)
from app.runtime.recovery import (
    FailureClass,
    classify_failure,
    maybe_refresh_auth,
    recovery_message,
)
from app.runtime.tools import ToolSpec, build_tool_specs, specs_for_model
from app.runtime.verifier import (
    TERMINAL_STATES,
    apply_verdict_to_terminal,
    user_facing_message,
    verify_completion,
)
from app.runtime.list_answer import prefer_list_answer

logger = structlog.get_logger("onsite48.runtime.loop")

import os as _os

def _env_int(name: str, default: int) -> int:
    try:
        return int(_os.environ.get(name, "") or default)
    except ValueError:
        return default

def _env_float(name: str, default: float) -> float:
    try:
        return float(_os.environ.get(name, "") or default)
    except ValueError:
        return default

# Global safety rails (per-run hard caps). Per-role working budgets live in
# roles_v2.py and typically sit below these. Raise via env, not code edits.
DEFAULT_MAX_STEPS = _env_int("AGENT_MAX_STEPS", 60)
DEFAULT_MAX_TOKENS = _env_int("AGENT_MAX_TOKENS", 400_000)
DEFAULT_MAX_COST_USD = _env_float("AGENT_MAX_COST_USD", 10.0)
DEFAULT_MAX_WALL_S = _env_float("AGENT_MAX_WALL_S", 1200)

# Progress heuristics. Static counters are a blunt instrument: verification
# loops (build→fix→rebuild) legitimately repeat calls. Budgets above are the
# real guard; these exist to catch true spinning early.
LOOP_SAME_CALL_LIMIT = _env_int("AGENT_SAME_CALL_LIMIT", 4)
LOOP_NO_PROGRESS_LIMIT = _env_int("AGENT_NO_PROGRESS_LIMIT", 8)

_LOOP_SAME_CALL_LIMIT_OVERRIDES: dict[str, int] = {
    # Execution verbs with genuine retry semantics get a wider berth.
    "python_exec": 8,
    "shell_exec": 8,
    "browser_act": 6,       # single page interactions can legitimately repeat
    "navigate": 5,
}


def _default_finish_summary(state: Any, goal: str, key_results: list[dict[str, Any]]) -> str:
    """Derive a user-facing summary when the agent finishes without one.

    An agent that calls finish with an empty summary (for example, a website
    that was built and packaged without a written summary) must still deliver
    *some* answer to the user. This builds a short, honest summary from the goal
    and the most recent tool results instead of leaving the completion event
    with no message at all.
    """
    goal_text = (goal or "").strip()
    if not goal_text:
        return "The task has been completed."

    # Surface the most recent successful tool result when it carries a
    # human-readable message or artifact reference.
    recent = ""
    for entry in reversed(key_results or []):
        if not isinstance(entry, dict):
            continue
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        if result.get("error") or result.get("error_code"):
            continue
        message = str(result.get("message") or result.get("output") or "").strip()
        if message:
            recent = message
            break
        artifact = result.get("artifact")
        if isinstance(artifact, dict):
            name = str(artifact.get("name") or artifact.get("filename") or "").strip()
            if name:
                recent = f"Prepared artifact: {name}"
                break

    if recent:
        return f"{recent}"
    return "The task has been completed."


def _args_hash(args: dict) -> str:
    return hashlib.sha256(
        json.dumps(args, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def _result_fingerprint(result: Any) -> str:
    """Stable, bounded fingerprint of a tool result for progress tracking."""
    if isinstance(result, dict):
        try:
            raw = json.dumps(result, sort_keys=True, default=str)
        except (TypeError, ValueError):
            raw = repr(result)
    else:
        raw = str(result)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


def _structured_error(
    code: str, message: str, *, retryable: bool = False, suggestion: str = ""
) -> dict:
    return {
        "error_code": code,
        "message": message,
        "retryable": retryable,
        "suggestion": suggestion,
    }


def _exception_result(exc: Exception) -> dict[str, Any]:
    """Keep structured connector failures intact at the tool boundary.

    A provider failure used to be flattened to ``tool_error`` and then
    reclassified from whatever words appeared in its message. That permitted an
    unrelated browser action to enter connector OAuth recovery. Preserve the
    provider's safe machine fields instead; ordinary exceptions retain the
    generic tool-error contract.
    """
    try:
        from app.services.provider_errors import ProviderError

        if isinstance(exc, ProviderError):
            payload = exc.as_failure()
            payload["error_code"] = str(payload.pop("code", exc.code))
            return payload
    except Exception:
        # Failure serialization must never mask the original tool failure.
        pass
    return _structured_error("tool_error", str(exc), retryable=False)


async def _emit_live_event(event_emitter: Any, event: dict[str, Any]) -> None:
    """Forward a live UI event; a failed emission must never break the run."""
    if event_emitter is None:
        return
    try:
        emitted = event_emitter(event)
        if hasattr(emitted, "__await__"):
            await emitted
    except Exception as exc:
        logger.warning(
            "runtime.live_event_emit_failed",
            event_type=str(event.get("type")),
            error=str(exc)[:200],
        )


def _plan_step_description(name: str, args: dict[str, Any]) -> str:
    """A readable one-line label for a tool step in the live plan."""
    summary = ""
    for key in (
        "command", "cmd", "path", "file", "query", "url",
        "goal", "question", "message", "name",
    ):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            summary = value.strip()
            break
    if not summary:
        try:
            summary = json.dumps(args, default=str)
        except (TypeError, ValueError):
            summary = str(args)
    summary = summary.replace("\n", " ").strip()[:120]
    return f"{name}: {summary}" if summary else name


def _tool_result_output(result: Any) -> str:
    """Render a tool result as bounded text for the live step output."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result[:4000]
    try:
        return json.dumps(result, default=str)[:4000]
    except (TypeError, ValueError):
        return str(result)[:4000]


def _truncated_tool_args(args: Any) -> bool:
    """True when a tool call carries a large JSON payload the provider
    cut off at its response output limit.

    A truncated call arrives as ``{"_raw": "..."}`` once the model's
    tool-argument JSON fails to parse: it opens an object, streams a
    large string value, and never closes it. The raw string is long,
    starts with ``{`` and does not end with ``}`` — distinct from a
    merely malformed (short or properly closed) payload.
    """
    raw = args.get("_raw") if isinstance(args, dict) else None
    if not isinstance(raw, str):
        return False
    stripped = raw.strip()
    return len(stripped) > 500 and stripped.startswith("{") and not stripped.endswith("}")


def _auth_needs_input_payload(
    *,
    tool_name: str,
    result: Any,
    refresh: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a user-safe terminal response when connector access needs repair."""
    if isinstance(result, dict):
        message = str(result.get("user_message") or result.get("message") or "").strip()
        connector_id = str(result.get("connector_id") or "").strip()
        reconnect_required = bool(result.get("reconnect_required"))
    else:
        message = ""
        connector_id = ""
        reconnect_required = False
    subject = connector_id or tool_name
    if not message:
        message = f"The {subject} connection needs to be reconnected in Settings."
    if refresh and not refresh.get("refreshed") and not reconnect_required:
        message = f"{message} Automatic refresh was unavailable; reconnect the account in Settings."
    return {
        "summary": user_facing_message("needs_input", message),
        "status": "needs_input",
        "evidence_refs": [],
    }


def _terminal_return(
    status: str,
    result: Any,
    state: Any,
    *,
    run_id: str | None = None,
    specs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a terminal loop.run payload with top-level metrics for evals/runner."""
    out: dict[str, Any] = {
        "status": status,
        "result": result,
        "state": state.to_dict() if hasattr(state, "to_dict") else (state or {}),
        "step_count": getattr(state, "step_count", 0) or 0,
        "token_count": getattr(state, "token_count", 0) or 0,
        "cost_usd": getattr(state, "cost_usd", None),
    }
    if run_id is not None:
        out["run_id"] = run_id
    # Include tool usage analytics
    if specs is not None:
        tool_stats = {}
        for name, spec in specs.items():
            if hasattr(spec, 'get_usage_stats'):
                stats = spec.get_usage_stats()
                if stats["call_count"] > 0:
                    tool_stats[name] = stats
        if tool_stats:
            out["tool_usage"] = tool_stats
    return out


def _is_error_result(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    return bool(result.get("error_code") or result.get("error"))


def _extract_untrusted_values(result: Any) -> list[str]:
    """Collect string tokens from untrusted results for provenance tracking."""
    vals: list[str] = []
    if not isinstance(result, dict):
        return vals
    for key in (
        "to", "email", "recipient", "url", "href", "address", "from",
        "content", "body", "text", "subject", "link",
    ):
        v = result.get(key)
        if isinstance(v, str) and len(v) >= 3:
            vals.append(v)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str) and len(item) >= 3:
                    vals.append(item)
    return vals


async def _run_readback(
    spec: ToolSpec,
    args: dict[str, Any],
    result: Any,
    *,
    run_id: str,
    journal: Journal,
) -> dict[str, Any] | None:
    """Execute ToolSpec.readback after a successful mutation. Journals the outcome."""
    if spec.readback is None:
        return None
    if spec.side_effect not in ("write", "destructive", "external_send", "spend"):
        return None
    if _is_error_result(result):
        return None
    try:
        rb_args = dict(args)
        if isinstance(result, dict):
            rb_args["_prior_result"] = result
        rb = await asyncio.wait_for(spec.readback(**rb_args), timeout=min(spec.timeout_s, 30))
        if not isinstance(rb, dict):
            rb = {"verified": False, "observed": {"raw": str(rb)[:500]}, "expected_diff": "non-dict readback"}
        await journal.append(
            run_id,
            "readback_result",
            {
                "tool": spec.name,
                "verified": rb.get("verified"),
                "observed": rb.get("observed"),
                "expected_diff": rb.get("expected_diff"),
            },
        )
        return rb
    except Exception as exc:
        rb = {
            "verified": False,
            "observed": {},
            "expected_diff": f"readback exception: {exc}",
        }
        await journal.append(
            run_id,
            "readback_result",
            {"tool": spec.name, **rb},
        )
        return rb


async def run(
    run_id: str,
    goal: str,
    role_config: dict | None = None,
    *,
    registry: Any = None,
    tool_registry: Any = None,
    session_factory: Any = None,
    workspace: str | Path | None = None,
    journal: Journal | None = None,
    event_emitter: Any = None,
    model_override: str | None = None,
    model_router: Any = None,
    context_budget_chars: int | None = None,
    parent_run_id: str | None = None,
    system_prompt: str | None = None,
    tool_allowlist: tuple | list | None = None,
    max_steps: int | None = None,
    max_tokens: int | None = None,
    max_cost_usd: float | None = None,
    max_wall_s: float | None = None,
    cancel_event: Any = None,
    user_id: str | None = None,
    tenant_id: str | None = None,
    thread_id: str | None = None,
    confirmed_actions: dict[str, Any] | None = None,
    rejected_tool_call: dict[str, Any] | None = None,
    schedule_authorization: dict[str, Any] | None = None,
    **_extra: Any,
) -> dict[str, Any]:
    role_config = dict(role_config or {})
    if parent_run_id:
        role_config["parent_run_id"] = parent_run_id
    if system_prompt:
        role_config["system_prompt"] = system_prompt
    if tool_allowlist is not None:
        role_config["tool_allowlist"] = list(tool_allowlist)
    if user_id:
        role_config["user_id"] = user_id
    if tenant_id:
        role_config["tenant_id"] = tenant_id
        role_config["org_id"] = tenant_id
    if thread_id:
        role_config["thread_id"] = thread_id
    # Scheduled-task autonomy: tools the operator explicitly delegated
    # through the schedule's autonomy policy (additional_allowed_tools /
    # allowed_custom_mcp_tools) run without an interactive confirmation so
    # unattended executions do not deadlock on an approval no one can
    # resolve. The argument-provenance guard still applies, so a delegated
    # send/spend tool whose target value looks injected still requires
    # approval and safely pauses the unattended run.
    org_policy = org_policy_from_schedule(schedule_authorization)
    if registry is None and tool_registry is not None:
        registry = tool_registry
    if model_router is not None:
        try:
            from app.runtime.models import set_model_router
            set_model_router(model_router)
        except Exception:
            pass
    j = journal or default_journal
    if session_factory is not None:
        j.set_session_factory(session_factory)

    ws = Path(
        workspace
        or os.environ.get("AGENT_WORKSPACE")
        or f"/tmp/onsite48_workspace/{run_id}"
    )
    ws.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("AGENT_WORKSPACE", str(ws))
    os.environ["AGENT_RUN_ID"] = run_id

    # Persistent computer for this run
    try:
        from app.runtime.workspace import create_workspace

        computer = await create_workspace(run_id)
        snap = await computer.snapshot()
        logger.info("runtime.workspace.ready", run_id=run_id, snapshot=snap)
    except Exception as exc:
        logger.warning("runtime.workspace.create_failed", run_id=run_id, error=str(exc))
        computer = None

    state = await j.replay(run_id, goal=goal, role_config=role_config)
    # Follow-up context: if this is a follow-up and we have conversation history,
    # prepend it to messages so the model knows what "them" refers to.
    if not state.messages:
        conv_ctx = role_config.get("conversation_context") if role_config else ""
        is_follow = role_config.get("is_follow_up") if role_config else False
        if is_follow and conv_ctx:
            # conversation_context is a formatted transcript: "User: ...\nSynth: ..."
            for line in conv_ctx.split("\n"):
                if line.startswith("User: "):
                    state.messages.append({"role": "user", "content": line[6:]})
                elif line.startswith("Synth: "):
                    state.messages.append({"role": "assistant", "content": line[7:]})
    forced_approved_call: dict[str, Any] | None = None
    if not state.goal:
        state.goal = goal
    if state.status in ("finished", "failed") and state.finish_payload:
        pending = state.finish_payload.get("pending_call") if isinstance(state.finish_payload, dict) else None
        approval_wait = isinstance(pending, dict) and state.finish_payload.get("status") == "waiting_approval"
        rejected = rejected_tool_call or {}
        if approval_wait and rejected.get("tool_name") == pending.get("name") and rejected.get("tool_args") == pending.get("arguments"):
            declined = {
                "summary": f"You declined the requested {pending.get('name')} action. No website was published.",
                "status": "cancelled",
                "evidence_refs": [],
            }
            await j.append(run_id, "run_finished", declined)
            return _terminal_return("cancelled", declined, state, run_id=run_id)
        was_confirmed = False
        if approval_wait and pending:
            from app.agents.legacy_compat import confirmation_key
            approval_key = confirmation_key(
                str(role_config.get("thread_id") or thread_id or run_id),
                int(state.step_count or 0),
                str(pending.get("name") or ""),
                dict(pending.get("arguments") or {}),
            )
            was_confirmed = bool((confirmed_actions or {}).get(approval_key))
        if approval_wait and was_confirmed:
            # Re-submit the exact paused call without asking the model to restate
            # or widen the user's approved arguments.
            forced_approved_call = {
                "id": pending.get("call_id") or f"approved_{uuid.uuid4().hex[:8]}",
                "name": pending.get("name"),
                "arguments": dict(pending.get("arguments") or {}),
            }
            state.status = "running"
            state.finish_payload = None
        else:
            return _terminal_return(
                state.status, state.finish_payload, state, run_id=run_id
            )
    approved_tool_calls = dict(confirmed_actions or {})

    specs = build_tool_specs(registry, workspace_root=str(ws))
    # Filter tools by allowlist if present (workers inherit subset)
    _allow = (role_config or {}).get("tool_allowlist")
    if _allow is not None:
        allow_set = set(_allow) | {"finish", "ask_user"}
        specs = {k: v for k, v in specs.items() if k in allow_set}
    context = ContextBuilder(
        budget_chars=context_budget_chars or int(
            os.environ.get("AGENT_CONTEXT_BUDGET", "80000")
        ),
        workspace=ws,
        journal=j,
    )

    _rb = role_config.get("budget") or {}

    # Capture caller-explicitness BEFORE resolution.
    _explicit = {
        "steps": max_steps is not None,
        "tokens": max_tokens is not None,
        "cost": max_cost_usd is not None,
        "wall": max_wall_s is not None,
    }
    max_steps = int(
        max_steps if _explicit["steps"] else role_config.get("max_steps") or _rb.get("steps") or DEFAULT_MAX_STEPS
    )
    max_tokens = int(
        max_tokens if _explicit["tokens"] else role_config.get("max_tokens") or _rb.get("tokens") or DEFAULT_MAX_TOKENS
    )
    max_cost = float(
        max_cost_usd if _explicit["cost"] else role_config.get("max_cost_usd") or _rb.get("cost_usd") or DEFAULT_MAX_COST_USD
    )
    max_wall = float(
        max_wall_s if _explicit["wall"] else role_config.get("max_wall_s") or _rb.get("wall_s") or DEFAULT_MAX_WALL_S
    )

    # Adaptive scaling for complex goals (explicit caller budgets and role
    # configs are left untouched; only DEFAULT_* fallbacks scale).
    _goal_l = (goal or getattr(state, "goal", "") or "").lower()
    _complexity_hits = sum(
        1 for k in (
            " and ", "report", "website", "deck", "spreadsheet", "analyze",
            "compare", "research", "build", "each of", "all of", "multi",
        ) if k in _goal_l
    )
    if _complexity_hits >= 4:
        _mult = 2.0
    elif _complexity_hits >= 2:
        _mult = 1.5
    else:
        _mult = 1.0
    if _mult > 1.0 and not _explicit["steps"] and not role_config.get("max_steps") and not _rb.get("steps"):
        max_steps = min(int(max_steps * _mult), DEFAULT_MAX_STEPS * 2)
        max_wall = min(max_wall * _mult, DEFAULT_MAX_WALL_S * 2)

    # Worker budget floor: ensure workers (parent_run_id set) never receive less
    # than the research minimums unless explicitly configured.
    if parent_run_id and not _explicit["steps"] and max_steps < 30:
        max_steps = 30
    if parent_run_id and not _explicit["wall"] and max_wall < 600:
        max_wall = 600.0

    # Fail fast when the goal looks like research but no search/browse tool exists.
    _goal_l = (goal or getattr(state, "goal", None) or "").lower()
    _researchy = any(
        k in _goal_l
        for k in ("research", "look up", "search for", "find the top", "who are the", "list the top")
    )
    _search_tools = {
        n for n in specs
        if any(x in n.lower() for x in ("search", "web_", "browser_", "brave", "tavily", "http_get", "fetch"))
    }
    if _researchy and not _search_tools and not parent_run_id:
        finish_payload = {
            "summary": (
                "Web research tools are not available in this environment "
                "(search/MCP not configured). Configure a search provider or "
                "browser bridge, then retry."
            ),
            "status": "failed",
            "evidence_refs": [],
            "error_code": "search_unavailable",
            "retryable": True,
            "suggestion": "Enable web search or pair My Browser, then run the task again.",
        }
        await j.append(run_id, "run_failed", finish_payload)
        return _terminal_return("failed", finish_payload, state, run_id=run_id, specs=specs)

    started = time.monotonic()
    import asyncio as _asyncio
    cancel_event = cancel_event or _asyncio.Event()

    _cancel_thread_id = thread_id or (role_config or {}).get("thread_id")
    _registered_cancel = bool(_cancel_thread_id)
    if _registered_cancel:
        try:
            from app.services.task_execution_control import task_execution_control
            await task_execution_control.register_cancel_event(_cancel_thread_id, cancel_event)
        except Exception:
            _registered_cancel = False

    async def _unregister_cancel_event() -> None:
        if _registered_cancel and _cancel_thread_id:
            try:
                from app.services.task_execution_control import task_execution_control
                await task_execution_control.unregister_cancel_event(_cancel_thread_id)
            except Exception:
                pass

    from app.runtime.roles_v2 import RoleConfig as _RC, get_role as _get_role, LEAD as _LEAD
    from app.runtime.locks import ResourceBusy, get_lock_manager, resources_for_tool
    from app.runtime.spawn import SpawnManager

    try:
        _role_name = role_config.get("name") or ("lead" if not parent_run_id else "researcher")
        if _role_name in ("lead", "researcher", "browser_operator", "coder", "writer", "critic"):
            _role = _get_role(_role_name)
        else:
            _role = _LEAD
        overrides = {k: role_config[k] for k in ("tool_allowlist", "system_prompt_file", "budget", "may_spawn", "depth_cap") if k in role_config}
        if overrides:
            _role = _RC.from_dict({**_role.to_dict(), **overrides})
    except Exception:
        _role = _LEAD

    spawn_mgr = SpawnManager(
        parent_run_id=run_id,
        parent_role=_role,
        workspace_root=ws,
        loop_runner=None,
        journal=j,
        depth=1 if parent_run_id else 0,
        cancel_event=cancel_event,
    )

    async def _child_loop_runner(**kwargs):
        return await run(**kwargs)

    spawn_mgr.loop_runner = _child_loop_runner

    # Loop-detection bookkeeping
    recent_calls: list[tuple[str, str]] = []  # (name, args_hash)
    same_call_streak = 0
    no_progress_streak = 0
    last_progress_sig: str | None = None
    empty_response_nudges = 0
    finish_attempts = 0
    auth_refresh_attempted: set[str] = set()
    untrusted_values: list[str] = list(getattr(state, "untrusted_values", None) or [])
    readbacks_ledger: list[dict[str, Any]] = []
    key_results: list[dict[str, Any]] = []
    # Live plan tracking: every executed tool call becomes one step the
    # client watches appear and fill in as the run progresses.
    live_plan_steps: list[dict[str, Any]] = []
    tool_step_counter = 0

    if not state.messages:
        await j.append(run_id, "run_started", {"goal": goal, "role_config": role_config})

    while True:
        # Budget gates
        if cancel_event.is_set():
            await spawn_mgr.cancel_all()
            finish_payload = {
                "summary": "Run cancelled (parent or external cancel).",
                "status": "cancelled",
                "evidence_refs": [],
            }
            await j.append(run_id, "run_finished", finish_payload)
            await _unregister_cancel_event()
            return _terminal_return("cancelled", finish_payload, state, run_id=run_id, specs=specs)

        if state.step_count >= max_steps:
            finish_payload = {
                "summary": "Stopped: step budget exhausted",
                "status": "partial",
                "evidence_refs": [],
            }
            await j.append(run_id, "run_finished", finish_payload)
            return _terminal_return("partial", finish_payload, state, run_id=run_id, specs=specs)
        if state.token_count >= max_tokens:
            finish_payload = {
                "summary": "Stopped: token budget exhausted",
                "status": "partial",
                "evidence_refs": [],
            }
            await j.append(run_id, "run_finished", finish_payload)
            return _terminal_return("partial", finish_payload, state, run_id=run_id, specs=specs)
        if state.cost_usd >= max_cost:
            finish_payload = {
                "summary": "Stopped: cost budget exhausted",
                "status": "partial",
                "evidence_refs": [],
            }
            await j.append(run_id, "run_finished", finish_payload)
            return _terminal_return("partial", finish_payload, state, run_id=run_id, specs=specs)
        if (time.monotonic() - started) >= max_wall:
            finish_payload = {
                "summary": "Stopped: wall-time budget exhausted",
                "status": "partial",
                "evidence_refs": [],
            }
            await j.append(run_id, "run_finished", finish_payload)
            return _terminal_return("partial", finish_payload, state, run_id=run_id, specs=specs)

        messages = context.build(state, specs)
        tool_schemas = specs_for_model(specs)

        try:
            if forced_approved_call is not None:
                from types import SimpleNamespace
                forced_call = forced_approved_call
                forced_approved_call = None
                response = SimpleNamespace(
                    content="",
                    tool_calls=[forced_call],
                    usage={},
                    cost_usd=0.0,
                    path="explicit-approval-replay",
                    model="runtime",
                )
            else:
                call_task = asyncio.ensure_future(
                    model_complete(
                        messages,
                        tools=tool_schemas,
                        role="loop",
                        tool_choice="auto",
                        model_override=model_override,
                    )
                )
                cancel_watcher = asyncio.ensure_future(cancel_event.wait())
                try:
                    done, pending = await asyncio.wait(
                        {call_task, cancel_watcher},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if cancel_event.is_set() and call_task not in done:
                        call_task.cancel()
                        try:
                            await asyncio.wait_for(call_task, timeout=5.0)
                        except Exception:
                            pass
                        finish_payload = {
                            "summary": "Run cancelled by user.",
                            "status": "cancelled",
                            "evidence_refs": [],
                        }
                        await j.append(run_id, "run_finished", finish_payload)
                        await _unregister_cancel_event()
                        return _terminal_return("cancelled", finish_payload, state, run_id=run_id, specs=specs)
                    response = await call_task
                finally:
                    cancel_watcher.cancel()
        except asyncio.CancelledError:
            finish_payload = {
                "summary": "Run cancelled by user.",
                "status": "cancelled",
                "evidence_refs": [],
            }
            await j.append(run_id, "run_finished", finish_payload)
            await _unregister_cancel_event()
            return _terminal_return("cancelled", finish_payload, state, run_id=run_id, specs=specs)
        except Exception as exc:
            logger.exception("runtime.loop.model_error", error=str(exc))
            finish_payload = {
                "summary": f"Model error: {exc}",
                "status": "failed",
                "evidence_refs": [],
            }
            await j.append(run_id, "run_failed", finish_payload)
            return _terminal_return("failed", finish_payload, state, run_id=run_id, specs=specs)

        await j.append(
            run_id,
            "model_response",
            {
                "tokens": (response.usage or {}).get("input_tokens", 0)
                + (response.usage or {}).get("output_tokens", 0),
                "cost_usd": response.cost_usd,
                "path": response.path,
                "model": response.model,
                "message": {
                    "role": "assistant",
                    "content": response.content,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"]),
                            },
                        }
                        for tc in response.tool_calls
                    ]
                    or None,
                },
            },
        )
        state.messages.append(
            {
                "role": "assistant",
                "content": response.content,
                "tool_calls": response.tool_calls or None,
            }
        )
        state.step_count += 1
        state.token_count += int((response.usage or {}).get("input_tokens", 0)) + int(
            (response.usage or {}).get("output_tokens", 0)
        )
        state.cost_usd += float(response.cost_usd or 0)

        # Live cost visibility: every model call updates the header
        # cost pill while the task is still running.
        await _emit_live_event(event_emitter, {
            "type": "cost_update",
            "cost_metrics": {"total_cost_usd": round(float(state.cost_usd or 0), 6)},
            "transient": True,
        })

        if not response.tool_calls:
            empty_response_nudges += 1
            if empty_response_nudges == 1 and response.content:
                state.messages.append(
                    {
                        "role": "user",
                        "content": (
                            "If the goal is complete, call the finish tool with summary and status. "
                            "Otherwise call a tool or ask_user."
                        ),
                    }
                )
                await j.append(run_id, "nudge", {"reason": "no_tool_calls"})
                continue
            finish_payload = {
                "summary": response.content or "Agent stopped without calling finish.",
                "status": "partial",
                "evidence_refs": [],
            }
            await j.append(run_id, "run_finished", finish_payload)
            return _terminal_return("partial", finish_payload, state, run_id=run_id, specs=specs)

        empty_response_nudges = 0

        for tc in response.tool_calls:
            name = tc.get("name") or ""
            args = tc.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw": args}
            call_id = tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
            spec = specs.get(name)

            await j.append(
                run_id,
                "tool_call_proposed",
                {"call_id": call_id, "name": name, "arguments": args},
            )

            if spec is None:
                result = _structured_error(
                    "unknown_tool", f"Tool {name!r} is not registered"
                )
                msg = context.format_tool_result(
                    call_id=call_id,
                    name=name,
                    result=result,
                    spec=ToolSpec(
                        name=name,
                        description="",
                        parameters={},
                        side_effect="read",
                        idempotency="idempotent",
                        timeout_s=5,
                        max_output_chars=2000,
                        trust="trusted",
                    ),
                )
                state.messages.append(msg)
                await j.append(
                    run_id,
                    "tool_call_result",
                    {"call_id": call_id, "name": name, "result": result, "message": msg},
                )
                continue

            # ----- Policy (with provenance) -----
            run_state_dict = state.to_dict()
            run_state_dict["untrusted_values"] = untrusted_values
            run_state_dict["approved_contacts"] = (
                role_config.get("approved_contacts") or []
            )
            from app.agents.legacy_compat import confirmation_key
            approval_key = confirmation_key(
                str(role_config.get("thread_id") or thread_id or run_id),
                int(state.step_count or 0),
                name,
                args,
            )
            if approved_tool_calls.pop(approval_key, False):
                decision = PolicyDecisionResult(
                    Decision.ALLOW,
                    "the user approved this exact tool action",
                )
            else:
                decision = policy_evaluate(
                    {"name": name, "arguments": args},
                    spec,
                    run_state_dict,
                    org_policy=org_policy,
                )
            await j.append(
                run_id,
                "policy_decision",
                {
                    "call_id": call_id,
                    "name": name,
                    "decision": decision.decision.value,
                    "reason": decision.reason,
                    "metadata": decision.metadata,
                },
            )

            if decision.decision == Decision.DENY:
                result = _structured_error(
                    "policy_denied",
                    decision.reason,
                    retryable=False,
                    suggestion="Choose a different tool or ask_user",
                )
                msg = context.format_tool_result(
                    call_id=call_id, name=name, result=result, spec=spec
                )
                state.messages.append(msg)
                await j.append(
                    run_id,
                    "tool_call_result",
                    {"call_id": call_id, "name": name, "result": result, "message": msg},
                )
                continue

            if decision.decision in (Decision.APPROVAL, Decision.TAKEOVER):
                await j.append(
                    run_id,
                    "approval_requested",
                    {
                        "call_id": call_id,
                        "name": name,
                        "arguments": args,
                        "reason": decision.reason,
                        "metadata": decision.metadata,
                    },
                )
                finish_payload = {
                    "summary": (
                        f"Approval required for {name}: {decision.reason}. "
                        f"Provenance: {json.dumps(decision.metadata.get('provenance', []), default=str)[:500]}"
                    ),
                    "status": "waiting_approval",
                    "evidence_refs": [],
                    "pending_call": {"name": name, "arguments": args, "call_id": call_id},
                }
                await j.append(run_id, "run_finished", finish_payload)
                return _terminal_return(
                    "waiting_approval", finish_payload, state, run_id=run_id, specs=specs
                )

            # ----- spawn / wide_map (Phase 5) -----
            if name in ("spawn_agent", "spawn_agents", "await_agents", "wide_map"):
                await j.append(
                    run_id,
                    "tool_call_started",
                    {"call_id": call_id, "name": name, "arguments": args},
                )
                try:
                    if name == "spawn_agent":
                        handle = await spawn_mgr.spawn_agent(
                            role=args.get("role") or "researcher",
                            goal=args.get("goal") or "",
                            inputs=args.get("inputs") or [],
                            output_schema=args.get("output_schema"),
                            budget=args.get("budget"),
                        )
                        result = {
                            "worker_id": handle.worker_id,
                            "run_id": handle.run_id,
                            "role": handle.role,
                            "status": handle.status,
                            "parent_run_id": run_id,
                        }
                    elif name == "spawn_agents":
                        handles = await spawn_mgr.spawn_agents(args.get("specs") or [])
                        result = {
                            "handles": [
                                {
                                    "worker_id": h.worker_id,
                                    "run_id": h.run_id,
                                    "role": h.role,
                                    "status": h.status,
                                }
                                for h in handles
                            ]
                        }
                    elif name == "await_agents":
                        wids = args.get("worker_ids")
                        handles = list(spawn_mgr._handles.values())
                        if wids:
                            handles = [h for h in handles if h.worker_id in wids]
                        handles = await spawn_mgr.await_agents(
                            handles, timeout_s=args.get("timeout_s")
                        )
                        child_cost = spawn_mgr.total_child_cost()
                        state.cost_usd = float(state.cost_usd or 0) + child_cost
                        result = {
                            "workers": [
                                {
                                    "worker_id": h.worker_id,
                                    "run_id": h.run_id,
                                    "status": h.status,
                                    "summary": h.summary,
                                    "artifacts": h.artifacts,
                                    "cost_usd": h.cost_usd,
                                    "error": h.error,
                                }
                                for h in handles
                            ],
                            "total_child_cost_usd": child_cost,
                        }
                    else:
                        from app.runtime.spawn import wide_map as _wide_map
                        result = await _wide_map(
                            items=args.get("items") or [],
                            role=args.get("role") or "researcher",
                            instruction_template=args.get("instruction_template") or "{item}",
                            output_schema=args.get("output_schema"),
                            concurrency=int(args.get("concurrency") or 4),
                            per_item_budget=args.get("per_item_budget"),
                            parent_run_id=run_id,
                            parent_role=_role,
                            workspace_root=ws,
                            loop_runner=spawn_mgr.loop_runner,
                            journal=j,
                            depth=spawn_mgr.depth,
                            cancel_event=cancel_event,
                            reduce_role=args.get("reduce_role"),
                            reduce_instruction=args.get("reduce_instruction"),
                        )
                        state.cost_usd = float(state.cost_usd or 0) + float(
                            result.get("cost_usd") or 0
                        )
                except Exception as exc:
                    result = _structured_error(
                        "spawn_error",
                        f"{type(exc).__name__}: {exc}",
                        retryable=False,
                    )
                msg = context.format_tool_result(
                    call_id=call_id, name=name, result=result, spec=spec
                )
                state.messages.append(msg)
                await j.append(
                    run_id,
                    "tool_call_result",
                    {"call_id": call_id, "name": name, "result": result, "message": msg},
                )
                key_results.append({"tool": name, "result": result})
                continue

            # ----- resource locks for mutating tools -----
            lock_keys = resources_for_tool(name, args if isinstance(args, dict) else {})
            lock_holder = None
            lock_mgr = get_lock_manager()
            if lock_keys and getattr(spec, "side_effect", "read") != "read":
                try:
                    for lk in lock_keys:
                        lock_holder = await lock_mgr.acquire(
                            lk, run_id=run_id, wait_s=10.0
                        )
                except ResourceBusy as rb:
                    result = rb.to_dict()
                    msg = context.format_tool_result(
                        call_id=call_id, name=name, result=result, spec=spec
                    )
                    state.messages.append(msg)
                    await j.append(
                        run_id,
                        "tool_call_result",
                        {"call_id": call_id, "name": name, "result": result, "message": msg},
                    )
                    continue

            # ----- finish / ask_user special cases -----
            if name == "finish":
                finish_attempts += 1
                finish_status = args.get("status") or "completed"
                # normalize legacy needs_user
                if finish_status == "needs_user":
                    finish_status = "needs_input"
                finish_summary = args.get("summary") or ""
                finish_payload = {
                    "summary": finish_summary,
                    "status": finish_status,
                    "evidence_refs": args.get("evidence_refs") or [],
                }
                await j.append(
                    run_id,
                    "tool_call_started",
                    {"call_id": call_id, "name": name, "arguments": args},
                )

                # Independent verifier (except when already a non-success honest state)
                if finish_status in ("completed", "partial", "completed_unverified"):
                    # Collect journal events for evidence
                    journal_events = []
                    try:
                        if hasattr(j, "list_events"):
                            journal_events = await j.list_events(run_id)
                        elif hasattr(j, "events_for"):
                            journal_events = await j.events_for(run_id)
                    except Exception:
                        journal_events = []

                    verdict = await verify_completion(
                        goal=state.goal or goal,
                        finish_summary=finish_summary,
                        finish_status=finish_status,
                        journal_events=journal_events,
                        readbacks=readbacks_ledger,
                        key_results=key_results[-20:],
                        model_override=model_override,
                    )
                    terminal = apply_verdict_to_terminal(
                        finish_status,
                        verdict,
                        finish_summary,
                        finish_attempts=finish_attempts,
                    )
                    if terminal["status"] == "_retry_finish":
                        # Feed unmet requirements back to the model
                        retry_msg = {
                            "role": "user",
                            "content": (
                                "finish was rejected by the independent verifier. "
                                f"Unmet requirements: {terminal['unmet_requirements']}. "
                                f"Missing evidence: {terminal['missing_evidence']}. "
                                "Continue working, then call finish again."
                            ),
                        }
                        state.messages.append(retry_msg)
                        await j.append(
                            run_id,
                            "tool_call_result",
                            {
                                "call_id": call_id,
                                "name": name,
                                "result": {
                                    "rejected": True,
                                    "verifier": verdict,
                                    "unmet_requirements": terminal["unmet_requirements"],
                                },
                            },
                        )
                        await j.append(run_id, "nudge", {"reason": "verifier_reject"})
                        continue  # continue the outer while loop via for-break

                    final_status = terminal["status"]
                    final_summary = user_facing_message(
                        final_status, terminal["summary"]
                    )
                    # Prefer structured list/count rows over a meta-only finish summary.
                    final_summary = prefer_list_answer(
                        state.goal or goal,
                        final_summary,
                        key_results,
                    )
                    # An agent that calls finish with an empty summary (for example,
                    # a website that was built and packaged without a written summary)
                    # must still deliver *some* answer to the user. Derive a default
                    # from the goal and recent tool results rather than leaving the
                    # completion event with no message at all.
                    if not final_summary:
                        final_summary = _default_finish_summary(state, goal, key_results)
                    finish_payload = {
                        "summary": final_summary,
                        "status": final_status,
                        "evidence_refs": args.get("evidence_refs") or [],
                        "verifier": verdict,
                        "unmet_requirements": terminal.get("unmet_requirements") or [],
                        "missing_evidence": terminal.get("missing_evidence") or [],
                    }
                else:
                    # needs_input / waiting_approval / failed / cancelled — trust agent declaration
                    final_status = finish_status if finish_status in TERMINAL_STATES else "failed"
                    finish_payload["status"] = final_status
                    rewritten = user_facing_message(final_status, finish_summary)
                    if final_status in ("completed", "completed_unverified", "partial"):
                        rewritten = prefer_list_answer(
                            state.goal or goal, rewritten, key_results
                        )
                    if not rewritten:
                        rewritten = _default_finish_summary(state, goal, key_results)
                    finish_payload["summary"] = rewritten

                await j.append(
                    run_id,
                    "tool_call_result",
                    {"call_id": call_id, "name": name, "result": finish_payload},
                )
                await j.append(run_id, "run_finished", finish_payload)
                await _emit_live_event(event_emitter, {
                    "type": "task_complete",
                    "status": finish_payload["status"],
                    "final_answer": finish_payload.get("summary"),
                    "verification": finish_payload.get("verifier"),
                    "cost_metrics": {"total_cost_usd": round(float(state.cost_usd or 0), 6)},
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                })
                return _terminal_return(
                    finish_payload["status"], finish_payload, state, run_id=run_id, specs=specs
                )

            if name == "ask_user":
                await j.append(
                    run_id,
                    "tool_call_started",
                    {"call_id": call_id, "name": name, "arguments": args},
                )
                payload = {
                    "question": args.get("question"),
                    "options": args.get("options"),
                }
                await j.append(
                    run_id,
                    "tool_call_result",
                    {"call_id": call_id, "name": name, "result": payload},
                )
                finish_payload = {
                    "summary": user_facing_message(
                        "needs_input",
                        args.get("question") or "Need user input",
                    ),
                    "status": "needs_input",
                    "evidence_refs": [],
                }
                await j.append(run_id, "run_finished", finish_payload)
                return _terminal_return(
                    "needs_input", payload, state, run_id=run_id, specs=specs
                )

            # ----- Loop detection -----
            ah = _args_hash(args if isinstance(args, dict) else {})
            sig = (name, ah)
            if recent_calls and recent_calls[-1] == sig:
                same_call_streak += 1
            else:
                same_call_streak = 1
            recent_calls.append(sig)
            if len(recent_calls) > 20:
                recent_calls = recent_calls[-20:]

            # ----- Execute -----
            await j.append(
                run_id,
                "tool_call_started",
                {"call_id": call_id, "name": name, "arguments": args},
            )
            state.last_tool_started = {
                "call_id": call_id,
                "name": name,
                "arguments": args,
            }
            # The step joins the live plan as it starts, with an empty
            # output placeholder the client replaces when it completes.
            tool_step_index = tool_step_counter
            tool_step_counter += 1
            step_description = _plan_step_description(name, args if isinstance(args, dict) else {})
            live_plan_steps.append({
                "step_id": tool_step_index,
                "description": step_description,
                "tool_name": name,
            })
            await _emit_live_event(event_emitter, {
                "type": "planner_output",
                "content": [dict(step) for step in live_plan_steps],
            })
            await _emit_live_event(event_emitter, {
                "type": "executor_output_delta",
                "content": {
                    "step_index": tool_step_index,
                    "step": step_description,
                    "tool": name,
                    "output": "",
                },
                "transient": True,
            })
            try:
                if spec._execute is None:
                    result = _structured_error(
                        "not_executable", f"{name} has no executor"
                    )
                else:
                    import time as _time
                    tool_start = _time.monotonic()
                    tool_task = asyncio.ensure_future(spec._execute(**args))
                    cancel_watcher = asyncio.ensure_future(cancel_event.wait())
                    try:
                        done, pending = await asyncio.wait(
                            {tool_task, cancel_watcher},
                            return_when=asyncio.FIRST_COMPLETED,
                            timeout=spec.timeout_s,
                        )
                        if cancel_event.is_set() and tool_task not in done:
                            tool_task.cancel()
                            try:
                                await asyncio.wait_for(tool_task, timeout=5.0)
                            except Exception:
                                pass
                            finish_payload = {
                                "summary": "Run cancelled by user.",
                                "status": "cancelled",
                                "evidence_refs": [],
                            }
                            await j.append(run_id, "run_finished", finish_payload)
                            await _unregister_cancel_event()
                            return _terminal_return("cancelled", finish_payload, state, run_id=run_id, specs=specs)
                        if tool_task in done:
                            result = await tool_task
                        else:
                            tool_task.cancel()
                            result = _structured_error(
                                "timeout",
                                f"{name} exceeded {spec.timeout_s}s",
                                retryable=True,
                            )
                    finally:
                        cancel_watcher.cancel()
                    # Record tool usage metrics
                    tool_latency_ms = (_time.monotonic() - tool_start) * 1000
                    success = not _is_error_result(result)
                    spec.record_call(success, tool_latency_ms)
            except asyncio.TimeoutError:
                tool_latency_ms = (_time.monotonic() - tool_start) * 1000
                spec.record_call(False, tool_latency_ms)
                result = _structured_error(
                    "timeout",
                    f"{name} exceeded {spec.timeout_s}s",
                    retryable=True,
                )
            except TypeError as exc:
                tool_latency_ms = (_time.monotonic() - tool_start) * 1000
                spec.record_call(False, tool_latency_ms)
                if _truncated_tool_args(args):
                    # The model tried to pass a payload larger than the
                    # provider's output budget, so the arguments were
                    # cut off before the tool could run. Repeating the
                    # same oversized call fails identically; point the
                    # model at a chunked strategy instead.
                    result = _structured_error(
                        "tool_args_truncated",
                        "The tool arguments were cut off by the model's response "
                        "output limit before the call could run; the content is too "
                        "large for a single response. Write large files in chunks: "
                        "create the file with the first part using file_write, then "
                        "append the rest with python_exec (open(path, 'a')).",
                        retryable=True,
                        suggestion="Split large file content across multiple python_exec append calls.",
                    )
                else:
                    result = _structured_error(
                        "bad_arguments",
                        str(exc),
                        retryable=True,
                        suggestion="Fix argument types/names",
                    )
            except Exception as exc:
                tool_latency_ms = (_time.monotonic() - tool_start) * 1000
                spec.record_call(False, tool_latency_ms)
                result = _exception_result(exc)

            # ----- Recovery classification -----
            same_call_limit = _LOOP_SAME_CALL_LIMIT_OVERRIDES.get(name, LOOP_SAME_CALL_LIMIT)
            action = classify_failure(
                result,
                tool_name=name,
                same_call_count=same_call_streak,
                no_progress_count=no_progress_streak,
                same_call_limit=same_call_limit,
                no_progress_limit=LOOP_NO_PROGRESS_LIMIT,
            )

            if action.failure_class == FailureClass.LOOP_DETECTED:
                finish_payload = {
                    "summary": user_facing_message(
                        "failed",
                        f"Loop detected on {name}: {action.suggestion}",
                    ),
                    "status": "failed",
                    "evidence_refs": [],
                    "recovery": recovery_message(action),
                }
                await j.append(
                    run_id,
                    "tool_call_result",
                    {"call_id": call_id, "name": name, "result": result},
                )
                await j.append(run_id, "run_finished", finish_payload)
                return _terminal_return(
                    "failed", finish_payload, state, run_id=run_id, specs=specs
                )

            if action.failure_class == FailureClass.AUTH_EXPIRED:
                # An explicit reconnect-required provider result is final: the
                # underlying request already tried its one safe refresh, and a
                # blind retry cannot obtain a new user grant.
                if not action.retryable:
                    finish_payload = _auth_needs_input_payload(tool_name=name, result=result)
                    await j.append(
                        run_id,
                        "tool_call_result",
                        {"call_id": call_id, "name": name, "result": result},
                    )
                    await j.append(run_id, "run_finished", finish_payload)
                    return _terminal_return("needs_input", finish_payload, state, run_id=run_id, specs=specs)

                if name in auth_refresh_attempted:
                    finish_payload = _auth_needs_input_payload(tool_name=name, result=result)
                    await j.append(
                        run_id,
                        "tool_call_result",
                        {"call_id": call_id, "name": name, "result": result},
                    )
                    await j.append(run_id, "run_finished", finish_payload)
                    return _terminal_return("needs_input", finish_payload, state, run_id=run_id, specs=specs)

                auth_refresh_attempted.add(name)
                refresh = await maybe_refresh_auth(
                    name,
                    org_id=(role_config or {}).get("org_id"),
                    connector_id=(str(result.get("connector_id")) if isinstance(result, dict) and result.get("connector_id") else None),
                )
                await j.append(
                    run_id,
                    "auth_refresh",
                    {"tool": name, "result": refresh},
                )
                if refresh.get("refreshed"):
                    # One automatic retry
                    try:
                        result = await asyncio.wait_for(
                            spec._execute(**args), timeout=spec.timeout_s
                        )
                        action = classify_failure(result, tool_name=name)
                    except Exception as exc:
                        result = _exception_result(exc)
                        action = classify_failure(result, tool_name=name)
                    if action.failure_class == FailureClass.AUTH_EXPIRED:
                        finish_payload = _auth_needs_input_payload(
                            tool_name=name,
                            result=result,
                            refresh=refresh,
                        )
                        await j.append(
                            run_id,
                            "tool_call_result",
                            {"call_id": call_id, "name": name, "result": result},
                        )
                        await j.append(run_id, "run_finished", finish_payload)
                        return _terminal_return("needs_input", finish_payload, state, run_id=run_id, specs=specs)
                else:
                    finish_payload = _auth_needs_input_payload(
                        tool_name=name,
                        result=result,
                        refresh=refresh,
                    )
                    await j.append(
                        run_id,
                        "tool_call_result",
                        {"call_id": call_id, "name": name, "result": result},
                    )
                    await j.append(run_id, "run_finished", finish_payload)
                    return _terminal_return(
                        "needs_input", finish_payload, state, run_id=run_id, specs=specs
                    )

            if action.retryable and action.backoff_s > 0:
                await asyncio.sleep(min(action.backoff_s, 30.0))

            # Attach recovery hint into the observation for the model
            if _is_error_result(result) and isinstance(result, dict):
                result = {**result, **recovery_message(action)}

            # ----- Read-back after successful mutation -----
            rb = await _run_readback(spec, args, result, run_id=run_id, journal=j)
            if rb is not None:
                readbacks_ledger.append(
                    {"tool": name, "call_id": call_id, **rb}
                )
                # Surface readback to the model as part of the observation
                if isinstance(result, dict):
                    result = {**result, "readback": rb}
                if rb.get("verified") is False:
                    # Mutation is unverified — model must see this
                    no_progress_streak += 1

            # Track progress signature. The result content is
            # part of the signature: two successful calls to the
            # same read tool with different arguments return
            # different data, and that is progress. Keying on
            # (name, ok, readback) alone made every successful
            # github_get_repository in a repo sweep look
            # identical, tripping the no-progress limit on a
            # legitimate iteration.
            progress_sig = json.dumps(
                {
                    "name": name,
                    "ok": not _is_error_result(result),
                    "rb": (rb or {}).get("verified"),
                    "result": _result_fingerprint(result),
                },
                sort_keys=True,
            )
            if progress_sig == last_progress_sig:
                no_progress_streak += 1
            else:
                no_progress_streak = 0
                last_progress_sig = progress_sig

            # Provenance: harvest untrusted values
            if spec.trust == "untrusted":
                untrusted_values.extend(_extract_untrusted_values(result))
                # Cap size
                if len(untrusted_values) > 500:
                    untrusted_values = untrusted_values[-500:]

            # Key results for verifier evidence
            if not _is_error_result(result):
                key_results.append(
                    {
                        "tool": name,
                        "call_id": call_id,
                        "result_summary": str(result)[:1000]
                        if not isinstance(result, dict)
                        else {
                            k: result[k]
                            for k in list(result)[:12]
                            if k != "readback"
                        },
                    }
                )

            if name == "file_write" and str(args.get("path") or "").endswith("todo.md"):
                state.todo_md = str(args.get("content") or "")[:8000]

            msg = context.format_tool_result(
                call_id=call_id, name=name, result=result, spec=spec
            )
            state.messages.append(msg)
            state.last_tool_started = None
            await j.append(
                run_id,
                "tool_call_result",
                {
                    "call_id": call_id,
                    "name": name,
                    "result": result,
                    "message": msg,
                    "recovery_class": action.failure_class.value
                    if _is_error_result(result)
                    else None,
                },
            )
            # The completed step's output is durable (a restored task
            # rebuilds its step results) and replaces the running
            # placeholder in the live plan accordion.
            await _emit_live_event(event_emitter, {
                "type": "executor_output",
                "content": {
                    "step_index": tool_step_index,
                    "step": step_description,
                    "tool": name,
                    "output": _tool_result_output(result),
                },
            })
            if event_emitter is not None and isinstance(result, dict):
                emitted_event = None
                if name == "preview_website" and isinstance(result.get("preview_url"), str):
                    emitted_event = {
                        "type": "website_preview",
                        "preview_id": result.get("preview_id"),
                        "preview_url": result.get("preview_url"),
                        "project_name": result.get("project_name") or "Website preview",
                        "expires_at": result.get("expires_at"),
                    }
                elif name == "package_website" and isinstance(result.get("artifact"), dict):
                    emitted_event = {"type": "artifact_created", "artifact": result["artifact"]}
                if emitted_event is not None:
                    await _emit_live_event(event_emitter, emitted_event)

    # unreachable
    if _registered_cancel:
        try:
            from app.services.task_execution_control import task_execution_control
            await task_execution_control.unregister_cancel_event(thread_id)
        except Exception:
            pass
    return _terminal_return(
        "failed", {"summary": "loop exited"}, state, run_id=run_id, specs=specs
    )
