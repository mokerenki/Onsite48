# backend/app/api/websocket_handler.py

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, AsyncGenerator, Optional, Set
import traceback

import structlog
from fastapi import WebSocket, WebSocketDisconnect
from langchain_core.messages import HumanMessage

from app.core.model_router_kimi_deepseek import KimiDeepSeekRouter
from app.memory.cognee_setup import CogneeMemory
from app.memory.retriever_tool import MemoryRetrieverTool
from app.services.browser_automation_service import BrowserAutomationService
from app.tools.browser_tool import BrowserTool
from app.tools.python_repl import PythonREPLTool
from app.tools.registry import ToolRegistry
from app.agents.router import DomainRouter
from app.agents.legacy_compat import confirmation_key
from app.mcp_clients.mcp_registry import MCPRegistry
from app.services.runtime_builder import build_task_graph
from app.graph import create_graph
from app.core import instances
from app.core.exceptions import ToolConfirmationRequired, UserInterventionRequired
from app.core.task_budgets import initial_task_budgets
from app.core.errors import TaskErrorCode, is_retryable_error, to_user_error
from app.sandbox.manager import SandboxManager, set_active_sandbox_manager, set_active_task_id
from app.core.persistence import persist_task_progress
from app.core.execution_identity import evidence_digest
from app.core.context import onsite48
from app.services.execution_identity import ExecutionIdentity, reset_execution_identity, set_execution_identity
from app.services.task_execution_control import task_execution_control

logger = structlog.get_logger("aether.websocket_handler")

# ─── Track active streams for cleanup ────────────────────────────
_active_streams: Set[str] = set()
_stream_lock = asyncio.Lock()


async def close_task_agent_computer(tool_registry: ToolRegistry | None, thread_id: str) -> None:
    """Close one task-scoped isolated browser context without touching other tasks.

    Agent Computer contexts are intentionally keyed by thread. A CAPTCHA handoff
    directs the user to their own browser and Onsite48 Bridge, so retaining the
    server-side browser after that point only leaks stale state into the UI and
    consumes an unnecessary isolated context.
    """
    if tool_registry is None or not thread_id:
        return
    for tool_name in ("computer_navigate", "computer_read_page", "computer_inspect"):
        tool = tool_registry.get(tool_name)
        computer = getattr(tool, "computer", None) if tool is not None else None
        close = getattr(computer, "close", None)
        if callable(close):
            try:
                await close(thread_id)
            except Exception as exc:
                logger.warning("agent_computer.close_failed", thread_id=thread_id, error=str(exc))
            finally:
                await task_execution_control.clear(thread_id)
            return
    await task_execution_control.clear(thread_id)


async def get_task_state_from_checkpoint(thread_id: str) -> Optional[dict]:
    """Retrieve the last checkpoint state for a thread."""
    ctx = onsite48
    try:
        config = {"configurable": {"thread_id": thread_id}}
        graph = build_task_graph(ctx)
        snapshot = await graph.aget_state(config)
        if snapshot is not None and snapshot.values:
            return dict(snapshot.values)
    except Exception as e:
        logger.error(f"Failed to get checkpoint for {thread_id}: {e}")
    return None


async def resume_task_from_checkpoint(
    thread_id: str,
    extra_state: Optional[dict] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Resume a task from its last checkpoint."""
    ctx = onsite48
    
    state = await get_task_state_from_checkpoint(thread_id)
    if not state:
        yield {
            "type": "error",
            "message": f"No checkpoint found for thread {thread_id}",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        return
    
    resumable_statuses = {"paused", "waiting_for_approval", "waiting_for_user"}
    current_status = str(state.get("status") or "")
    # Bridge intervention often leaves user_intervention set while status is paused;
    # approval gates use waiting_for_approval. Both must be resumeable after the user acts.
    has_intervention = isinstance(state.get("user_intervention"), dict)
    if current_status not in resumable_statuses and not has_intervention:
        yield {
            "type": "error",
            "message": f"Task {thread_id} is not paused (status: {current_status or 'unknown'})",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        return
    
    if extra_state:
        state.update(extra_state)
    
    # ─── FIX: Reset volatile state fields on resume ──────────────────
    # This prevents stale state from causing issues after resume
    volatile_fields = {"done", "needs_replan", "replan_feedback", "final_answer", "user_intervention", "paused_at", "waiting_confirmation", "waiting_for_clarification", "clarification_question"}
    for field in volatile_fields:
        state.pop(field, None)
    
    state["status"] = "running"
    state["execution_state"] = "running"
    state["resumed_at"] = datetime.now(timezone.utc).isoformat()
    
    graph = build_task_graph(ctx)
    
    config = {"configurable": {"thread_id": thread_id}}
    
    try:
        # Write the resumed status before streaming so a second Resume click
        # does not race against a still-"paused" checkpoint.
        await graph.aupdate_state(config, state, as_node="planner")
        async for event in graph.astream(None, config=config):
            event_type = list(event.keys())[0]
            node_output = event[event_type]
            yield {
                "type": "node_output",
                "node": event_type,
                "content": node_output,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            await persist_task_progress(thread_id, node_output)
        
        snapshot = await graph.aget_state(config)
        final_state = snapshot.values if snapshot else state
        
        yield {
            "type": "task_complete",
            "status": final_state.get("status", "completed"),
            "final_answer": final_state.get("final_answer"),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except ToolConfirmationRequired as e:
        # Propagate so callers that use stream_task_events path stay consistent;
        # this legacy resume helper surfaces a clear error instead.
        yield {
            "type": "error",
            "message": f"Approval required for {e.tool_name}. Use the confirm action instead of Resume.",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except UserInterventionRequired as e:
        yield {
            "type": "needs_user_intervention",
            "reason": e.reason,
            "url": e.url,
            "title": e.title,
            "step_description": e.step_description,
            "step_index": e.step_index,
            "handoff_options": e.handoff_options,
            "handoff_surface": e.handoff_surface,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.exception(f"Error resuming task {thread_id}")
        yield {
            "type": "error",
            "message": f"Error resuming task: {str(e)}",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }


async def get_paused_task_state(thread_id: str) -> Optional[dict]:
    """Get the full state of a paused / intervention / approval-gated task."""
    state = await get_task_state_from_checkpoint(thread_id)
    if state is None:
        return None
    status = str(state.get("status") or "")
    resumable = status in {"paused", "waiting_for_approval", "waiting_for_user"}
    has_intervention = isinstance(state.get("user_intervention"), dict)
    has_waiting = isinstance(state.get("waiting_confirmation"), dict)
    if not (resumable or has_intervention or has_waiting):
        return None
    return {
        "thread_id": thread_id,
        "task": state.get("task"),
        "plan": state.get("plan", []),
        "current_step": state.get("current_step", 0),
        "results": state.get("results", []),
        "verification": state.get("verification"),
        "cost_metrics": state.get("cost_metrics", {}),
        "final_answer": state.get("final_answer"),
        "paused_at": state.get("paused_at"),
        "status": status,
        "user_intervention": state.get("user_intervention"),
        "waiting_confirmation": state.get("waiting_confirmation"),
    }


def terminal_event_for_state(final_state: dict) -> dict:
    """Convert durable graph terminal state into one unambiguous client event."""
    now = datetime.now(timezone.utc).isoformat()
    task_error = final_state.get("task_error") or {}
    status = final_state.get("status", "completed")
    results = final_state.get("results") if isinstance(final_state.get("results"), list) else []
    evidence = [item.get("evidence") for item in results if isinstance(item, dict) and isinstance(item.get("evidence"), dict)]
    verification = final_state.get("verification") if isinstance(final_state.get("verification"), dict) else {}
    proof = {
        "plan_revision_id": final_state.get("plan_revision_id"),
        "verification_status": verification.get("status"),
        "evidence_count": len(evidence),
        "evidence_digest": evidence_digest({"revision": final_state.get("plan_revision_id"), "evidence": evidence}) if evidence else None,
        "claimable": status == "completed" and verification.get("status") in {"PASS", "NOT_REQUIRED"},
    }
    if status == "failed":
        return {
            "type": "task_error",
            "task_id": final_state.get("task_id"),
            "error_code": task_error.get("code") or task_error.get("error_code") or "task_failed",
            "message": final_state.get("final_answer") or task_error.get("message") or "The task could not be completed.",
            "error": task_error.get("message"),
            "completion_proof": proof,
            "timestamp": now,
        }
    if status == "paused" and final_state.get("waiting_for_clarification"):
        return {
            "type": "needs_clarification",
            "task_id": final_state.get("task_id"),
            "thread_id": final_state.get("thread_id"),
            "question": final_state.get("clarification_question") or final_state.get("final_answer") or "Please clarify the request.",
            "step_index": final_state.get("current_step"),
            "timestamp": now,
        }
    pending = final_state.get("waiting_confirmation") or final_state.get("pending_call")
    if status == "waiting_approval" and isinstance(pending, dict):
        return {
            "type": "needs_confirmation",
            "task_id": final_state.get("task_id"),
            "thread_id": final_state.get("thread_id"),
            "tool_name": pending.get("name") or pending.get("tool_name"),
            "tool_args": pending.get("arguments") or pending.get("tool_args") or {},
            "step_description": (
                "Publish this website to the configured provider. Review the project path and target before approving."
                if (pending.get("name") or pending.get("tool_name")) == "publish_website"
                else "Review the exact action details before approving this external action."
            ),
            "step_index": int(final_state.get("step_count") or 0),
            "task_scoped_read_only_available": False,
            "timestamp": now,
        }
    return {
        "type": "task_complete",
        "task_id": final_state.get("task_id"),
        "thread_id": final_state.get("thread_id"),
        "status": status,
        "final_answer": final_state.get("final_answer"),
        "plan": final_state.get("plan"),
        "verification": final_state.get("verification"),
        "completion_proof": proof,
        "cost_metrics": final_state.get("cost_metrics"),
        "timestamp": now,
    }


def _website_tool_result(step: Any) -> tuple[str, dict[str, Any]]:
    """Extract a structured website tool result from an executor step record."""
    if not isinstance(step, dict):
        return "", {}
    tool_name = str(step.get("tool") or step.get("tool_name") or step.get("name") or "")
    value = step.get("output", step.get("result", step.get("tool_data")))
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return tool_name, {}
    if isinstance(value, dict) and isinstance(value.get("result"), dict):
        value = value["result"]
    return tool_name, value if isinstance(value, dict) else {}


def _live_stream_event(event: dict[str, Any], *, task_id: str, thread_id: str) -> dict[str, Any]:
    """Enrich a runtime loop event for the client stream.

    The loop emits presentation events (plan growth, tool output,
    running cost) without knowing the task identity; the stream
    stamps it here so every frame the client receives is complete.
    """
    formatted = dict(event)
    formatted.setdefault("task_id", task_id)
    formatted.setdefault("thread_id", thread_id)
    formatted.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    return formatted


async def stream_task_events(
    user_message: str,
    user_id: str,
    tenant_id: str,
    thread_id: str,
    checkpointer: Any,
    model_router: KimiDeepSeekRouter,
    tool_registry: Optional[ToolRegistry] = None,
    domain_router: Optional[DomainRouter] = None,
    browser_service: Optional[BrowserAutomationService] = None,
    extra_state: Optional[Dict[str, Any]] = None,
    display_message: Optional[str] = None,
    resume_from_checkpoint: bool = False,
    reset_checkpoint: bool = False,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Stream task execution events via WebSocket with proper cleanup and timeout handling.
    """
    # ─── Track this stream ──────────────────────────────────────────
    # One live stream per thread. Two concurrent streams over the same thread
    # both drive the graph against the same checkpoint, so a mutating step
    # (sending an email, deleting a draft) can execute twice. The duplicate was
    # previously logged and then admitted anyway.
    async with _stream_lock:
        duplicate = thread_id in _active_streams
        if not duplicate:
            _active_streams.add(thread_id)

    if duplicate:
        logger.warning("websocket.duplicate_stream_rejected", thread_id=thread_id)
        yield {
            "type": "error",
            "thread_id": thread_id,
            "message": "This task is already running in another window.",
        }
        return

    task_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    sandbox_manager = SandboxManager()
    sandbox_task_id = f"task:{task_id}"

    # Use global instances if not provided
    if tool_registry is None:
        tool_registry = instances.tool_registry
    if domain_router is None:
        domain_router = instances.domain_router
    if checkpointer is None:
        checkpointer = instances.checkpointer
    if model_router is None:
        model_router = instances.model_router

    # Validate required dependencies
    if tool_registry is None or domain_router is None or checkpointer is None or model_router is None:
        logger.error("stream_task_events.missing_dependencies")
        yield {
            "type": "task_error",
            "error": "Application dependencies not fully initialized. Please try again later.",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        async with _stream_lock:
            _active_streams.discard(thread_id)
        return

    # Live event bus. The runtime loop emits UI events (plan growth,
    # tool output, running cost) while it works, but the graph node
    # only returns once the whole loop has finished. A queue lets the
    # stream forward those events as they happen instead of holding
    # them until completion.
    live_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def live_event_emitter(event: dict[str, Any]) -> None:
        await live_events.put(event)

    # Create the graph instance for this task
    # Keep the legacy symbol as a narrow test/integration seam while the
    # runtime dependencies themselves remain canonical and shared.
    graph = create_graph(model_router, domain_router, tool_registry, checkpointer, live_event_emitter=live_event_emitter)

    if browser_service:
        logger.debug("websocket_handler.browser_service_available")

    # Follow-ups share the conversation thread_id. Without clearing, LangGraph
    # can resume channel values (done, plan, results) from the previous run and
    # either no-op or plan against stale goals.
    if reset_checkpoint and not resume_from_checkpoint:
        try:
            if hasattr(checkpointer, "delete_thread"):
                deleted = await checkpointer.delete_thread(thread_id)
                logger.info("websocket.checkpoint_reset_for_follow_up", thread_id=thread_id, deleted=deleted)
        except Exception as exc:
            logger.warning("websocket.checkpoint_reset_failed", thread_id=thread_id, error=str(exc))

    # ─── FIX: Reset volatile state fields on fresh start ────────────
    initial_state = {
        "task_id": task_id,
        "thread_id": thread_id,
        "task": user_message,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "messages": [HumanMessage(content=user_message)],
        "plan": [],
        "current_step": 0,
        "results": [],
        "prior_results": [],
        "tool_calls": [],
        "verification": None,
        "needs_replan": False,
        "done": False,
        "final_answer": None,
        "status": "running",
        "cost_metrics": {
            "total_cost_usd": 0.0,
            "known_model_calls": 0,
            "unknown_cost_model_calls": 0,
            "by_provider": {},
            "tool_calls": 0,
        },
        **initial_task_budgets(),
        "scratchpad": "",
        "confirmed_actions": {},
        "skipped_steps": {},
        "conversation_context": "",
        "task_metadata": {},
        "is_follow_up": False,
    }
    config = {"configurable": {"thread_id": thread_id}}
    execution_generation = await task_execution_control.snapshot_generation(thread_id)
    was_waiting_for_clarification = False
    
    if resume_from_checkpoint:
        snapshot = await graph.aget_state(config)
        if snapshot is None or not snapshot.values:
            yield {
                "type": "task_error", 
                "error_code": "resume_checkpoint_missing", 
                "message": "The approved task state is no longer available. Please run the task again.", 
                "timestamp": now
            }
            async with _stream_lock:
                _active_streams.discard(thread_id)
            return
        initial_state = dict(snapshot.values)
        # ─── FIX: Reset volatile fields on resume ──────────────────
        was_waiting_for_clarification = bool(initial_state.get("waiting_for_clarification"))
        volatile_fields = {"done", "needs_replan", "replan_feedback", "final_answer", "user_intervention", "paused_at", "waiting_confirmation", "waiting_for_clarification", "clarification_question"}
        for field in volatile_fields:
            initial_state.pop(field, None)
        initial_state.update(extra_state or {})
        initial_state["status"] = "running"
        initial_state["execution_state"] = "running"
        initial_state["resumed_at"] = now
    elif extra_state:
        initial_state.update(extra_state)

    # The task text is an execution invariant. A legacy or incomplete
    # checkpoint must recover it from this invocation before any graph node is
    # called. This recovery must happen before a resumed state is written back
    # to LangGraph; otherwise graph.astream(None) resumes with its stale blank
    # task field and the planner receives an empty Overall Goal.
    resolved_task = str(initial_state.get("task") or user_message or "").strip()
    if resume_from_checkpoint and was_waiting_for_clarification and user_message.strip():
        resolved_task = f"{resolved_task}\n\nUser clarification: {user_message.strip()}"
    if not resolved_task:
        yield {
            "type": "task_error",
            "error_code": "task_context_missing",
            "message": "Synth could not recover the original task description. Please run the task again.",
            "timestamp": now,
        }
        async with _stream_lock:
            _active_streams.discard(thread_id)
        return
    from app.runtime.website_build import is_website_build_request, website_build_preamble
    website_build_intent = is_website_build_request(resolved_task)
    if website_build_intent and "[WEBSITE BUILD WORKFLOW]" not in resolved_task:
        resolved_task = f"{resolved_task}{website_build_preamble()}"
        metadata = dict(initial_state.get("task_metadata") or {})
        metadata["intent"] = "website_build"
        metadata["preview_mode"] = "static_isolated"
        initial_state["task_metadata"] = metadata
    initial_state["task"] = resolved_task
    
    # A checkpoint created by an interrupted executor can contain only the
    # latest node writes. Restore the execution identity before writing a
    # resumed state back to LangGraph. In particular, confirmation_key() is
    # thread-bound: losing thread_id makes an otherwise exact approval appear
    # unapproved and restarts the Bridge-read confirmation loop.
    initial_state["task_id"] = str(initial_state.get("task_id") or task_id)
    initial_state["thread_id"] = thread_id
    initial_state["user_id"] = user_id
    initial_state["tenant_id"] = tenant_id
    if not initial_state.get("messages"):
        initial_state["messages"] = [HumanMessage(content=resolved_task)]

    if resume_from_checkpoint:
        # The prior checkpoint is the planner output immediately before the
        # confirmation exception. Marking that node as updated lets LangGraph
        # continue through its normal planner route into the saved executor.
        await graph.aupdate_state(config, initial_state, as_node="planner")

    yield {
        "type": "task_start", 
        "task_id": initial_state.get("task_id", task_id), 
        "message": display_message or user_message, 
        "domain": "website_build" if website_build_intent else None,
        "timestamp": now, 
        "resumed": resume_from_checkpoint
    }

    execution_identity_token = None
    try:
        set_active_sandbox_manager(sandbox_manager)
        set_active_task_id(sandbox_task_id)
        initial_state["sandbox_id"] = sandbox_task_id
        execution_identity_token = set_execution_identity(
            ExecutionIdentity(
                user_id=uuid.UUID(user_id),
                organization_id=uuid.UUID(tenant_id),
                task_thread_id=thread_id,
            )
        )

        # A failing upstream model or tool must not leave the client on "Starting"
        # forever. Operators can extend this for long-running tasks in .env.
        timeout_seconds = max(60, int(os.getenv("TASK_EXECUTION_TIMEOUT_SECONDS", "900")))
        async with asyncio.timeout(timeout_seconds):
            # The graph node runs the whole agent loop and returns once,
            # so its outputs are collected in the background while live
            # events (plan, tool output, cost) are forwarded from the
            # queue as the loop emits them.
            astream_outputs: list[dict[str, Any]] = []

            async def collect_astream() -> None:
                # A resumed run must re-enter the loop node with the
                # restored state. Passing None would only continue from
                # the checkpoint, and a single-node graph that already
                # returned (an approval pause) has no next node to run,
                # so confirmed_actions never reaches loop.run and the
                # approved action is re-requested instead of executed.
                async for event in graph.astream(initial_state, config=config):
                    astream_outputs.append(event)

            astream_task = asyncio.create_task(collect_astream())
            try:
                while not astream_task.done():
                    if await task_execution_control.stream_was_superseded(thread_id, execution_generation):
                        logger.info("websocket.task_stream_superseded_by_user_takeover", thread_id=thread_id)
                        yield {
                            "type": "task_execution_interrupted",
                            "reason": "The user took control of Agent Computer.",
                            "thread_id": thread_id,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                        return
                    while True:
                        try:
                            live_event = live_events.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        yield _live_stream_event(live_event, task_id=task_id, thread_id=thread_id)
                    await asyncio.sleep(0.05)
                # Drain any event emitted just before the node returned.
                while not live_events.empty():
                    yield _live_stream_event(live_events.get_nowait(), task_id=task_id, thread_id=thread_id)
            finally:
                if not astream_task.done():
                    astream_task.cancel()

            # Surface a graph collection failure the way the direct
            # iteration would have.
            if not astream_task.cancelled():
                await astream_task

            # Node outputs arrive at completion. The runtime loop emits
            # its own events live, so these branches only fire for nodes
            # a future graph may add.
            for event in astream_outputs:
                if await task_execution_control.stream_was_superseded(thread_id, execution_generation):
                    logger.info("websocket.task_stream_superseded_by_user_takeover", thread_id=thread_id)
                    yield {
                        "type": "task_execution_interrupted",
                        "reason": "The user took control of Agent Computer.",
                        "thread_id": thread_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    return
                event_type = list(event.keys())[0]
                node_output = event[event_type]

                if event_type == "planner":
                    yield {
                        "type": "planner_output",
                        "content": node_output.get("plan"),
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                    await persist_task_progress(thread_id, node_output)
                
                elif event_type == "executor":
                    results = node_output.get("results", [])
                    if results:
                        last_result = results[-1]
                        yield {
                            "type": "executor_output",
                            "content": last_result,
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        }
                        tool_name, tool_result = _website_tool_result(last_result)
                        event_time = datetime.now(timezone.utc).isoformat()
                        if tool_name == "preview_website" and isinstance(tool_result.get("preview_url"), str):
                            yield {
                                "type": "website_preview",
                                "thread_id": thread_id,
                                "preview_id": tool_result.get("preview_id"),
                                "preview_url": tool_result.get("preview_url"),
                                "project_name": tool_result.get("project_name") or "Website preview",
                                "expires_at": tool_result.get("expires_at"),
                                "timestamp": event_time,
                            }
                        elif tool_name == "package_website" and isinstance(tool_result.get("artifact"), dict):
                            yield {
                                "type": "artifact_created",
                                "thread_id": thread_id,
                                "artifact": tool_result["artifact"],
                                "timestamp": event_time,
                            }
                    for website_event in node_output.get("website_events", []):
                        if not isinstance(website_event, dict) or website_event.get("type") not in {"website_preview", "artifact_created"}:
                            continue
                        yield {
                            **website_event,
                            "task_id": task_id,
                            "thread_id": thread_id,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    await persist_task_progress(thread_id, node_output)

                elif event_type == "verifier":
                    yield {
                        "type": "verifier_output",
                        "content": node_output.get("verification"),
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                    await persist_task_progress(thread_id, node_output)

                else:
                    logger.debug("unknown_graph_event", event_type=event_type)

                transitions = node_output.get("execution_transitions", []) if isinstance(node_output, dict) else []
                if transitions:
                    latest_transition = transitions[-1]
                    yield {
                        "type": "task_state",
                        "state": latest_transition.get("to"),
                        "reason": latest_transition.get("reason"),
                        "step_id": latest_transition.get("step_id"),
                        "details": latest_transition.get("details", {}),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }

        if await task_execution_control.stream_was_superseded(thread_id, execution_generation):
            logger.info("websocket.task_completion_suppressed_by_user_takeover", thread_id=thread_id)
            yield {
                "type": "task_execution_interrupted",
                "reason": "The user took control of Agent Computer.",
                "thread_id": thread_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            return
        
        snapshot = await graph.aget_state(config)
        final_state = snapshot.values if snapshot and snapshot.values else initial_state

        yield terminal_event_for_state(final_state)

    except TimeoutError:
        await close_task_agent_computer(tool_registry, thread_id)
        timeout_message = "The task exceeded the execution time limit. Please try a narrower request or run it again."
        logger.warning("websocket_task_execution_timed_out", thread_id=thread_id)
        yield {
            "type": "task_error",
            "error_code": "execution_timeout",
            "message": timeout_message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except ToolConfirmationRequired as exc:
        # Persist an explicit checkpoint state before informing the client. The
        # approval resume path then continues this exact plan/executor state.
        await graph.aupdate_state(config, {
            "status": "waiting_for_approval",
            "execution_state": "waiting_for_approval",
            "waiting_confirmation": {
                "tool_name": exc.tool_name,
                "tool_args": exc.tool_args,
                "step_index": exc.step_index,
                "step_description": exc.step_description,
            },
        }, as_node="planner")
        logger.info("websocket.confirmation_required", tool=exc.tool_name, step=exc.step_index)
        yield {
            "type": "needs_confirmation",
            "tool_name": exc.tool_name,
            "tool_args": exc.tool_args,
            "step_description": exc.step_description,
            "step_index": exc.step_index,
            "task_scoped_read_only_available": exc.task_scoped_read_only_available,
            "task_id": task_id,
            "thread_id": thread_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except UserInterventionRequired as exc:
        # Never solve or bypass a challenge. A challenge in the local Synth
        # Bridge is completed in the user's own browser, so its isolated agent
        # browser can close. A challenge discovered inside Agent Computer keeps
        # that one task-scoped context open for explicit user takeover.
        if exc.handoff_surface != "agent_computer":
            await close_task_agent_computer(tool_registry, thread_id)
        paused_at = datetime.now(timezone.utc).isoformat()
        await graph.aupdate_state(config, {
            "status": "paused",
            "execution_state": "paused",
            "paused_at": paused_at,
            "user_intervention": {
                "reason": exc.reason,
                "step_index": exc.step_index,
                "url": exc.url,
                "handoff_surface": exc.handoff_surface,
            },
        }, as_node="planner")
        logger.info("websocket.user_intervention_required", step=exc.step_index, url=exc.url, handoff_surface=exc.handoff_surface)
        yield {
            "type": "needs_user_intervention",
            "reason": exc.reason,
            "url": exc.url,
            "title": exc.title,
            "step_description": exc.step_description,
            "step_index": exc.step_index,
            "handoff_options": exc.handoff_options,
            "handoff_surface": exc.handoff_surface,
            "task_id": task_id,
            "thread_id": thread_id,
            "timestamp": paused_at,
        }

    except Exception as exc:
        await close_task_agent_computer(tool_registry, thread_id)
        code, user_message_error = to_user_error(exc)
        error_id = f"err-{uuid.uuid4().hex[:12]}"
        retryable = is_retryable_error(code)
        logger.exception(
            "websocket_task_execution_failed",
            error_id=error_id,
            error_code=code.value,
            retryable=retryable,
            error=str(exc),
        )
        yield {
            "type": "task_error",
            "error_code": code.value,
            "message": user_message_error,
            "error_id": error_id,
            "retryable": retryable,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    finally:
        # ─── Cleanup: Remove from active streams ──────────────────
        async with _stream_lock:
            _active_streams.discard(thread_id)
        
        if execution_identity_token is not None:
            reset_execution_identity(execution_identity_token)
        set_active_sandbox_manager(None)
        set_active_task_id(None)
        await sandbox_manager.destroy(sandbox_task_id)


async def websocket_endpoint(
    websocket: WebSocket,
    cognee_memory: CogneeMemory,
    checkpointer: Any,
    model_router: KimiDeepSeekRouter,
    browser_service: BrowserAutomationService,
    tool_registry: ToolRegistry,
    domain_router: DomainRouter,
):
    """Refuse the retired unauthenticated WebSocket protocol.

    Production clients must use the authenticated `/ws` gateway implemented in
    `app.api.realtime`. The older protocol accepted client-provided identity,
    plan, and confirmation data and therefore must not execute work.
    """
    del cognee_memory, checkpointer, model_router, browser_service, tool_registry, domain_router
    await websocket.close(code=1008, reason="Use the authenticated Onsite48 realtime gateway.")
