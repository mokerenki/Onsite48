from __future__ import annotations

import os
import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.websocket_handler import get_paused_task_state, resume_task_from_checkpoint, stream_task_events
from app.agents.legacy_compat import SAFE_TASK_SCOPED_TOOLS, confirmation_key
from app.models import AgentEvidence, AgentRun, AgentStepAttempt, BrowserSession, OrganizationMember, OutboxEvent, TaskEvent, TaskStatus, TaskThread, ToolApproval, User
from app.services.attachment_store import attachment_store
from app.services.fast_answer import select_fast_route, stream_fast_answer
from app.services.run_ledger import add_artifact, start_run
from app.services.task_persistence import StatusConflict, append_conversation_turn, conversation_for, fail_task_terminal, status_from_event, transition_status, utcnow
from app.core.tracing import bind_request_trace, current_trace_id
from app.services.task_lease import release_task_lease_by_id, refresh_task_lease
from app.services.report_delivery import prepare_report_delivery
from app.services.eval_viewer import get_eval_viewer
from app.services.browser_live_registry import browser_live_registry
from app.core.task_state_machine import (
    event_identity,
    lifecycle_snapshot,
    phase_for_event,
    should_apply_event,
    task_status_for_phase,
    validate_transition,
)
from app.core.task_presentation import concise_task_title, ensure_task_presentation

logger = structlog.get_logger("onsite48.realtime")


# A session in any of these states has passed an explicit user grant. The
# pending and terminal states are deliberately absent.
AUTHORIZED_SESSION_STATUSES = frozenset({"authorized", "running", "paused", "takeover"})

# A task in one of these is live work. A second browser tab, a second
# WebSocket, or a retry arriving while one is in flight must be refused rather
# than allowed to start a concurrent run against the same row.
ACTIVE_TASK_STATUSES = frozenset({
    TaskStatus.RUNNING,
    TaskStatus.QUEUED,
    TaskStatus.PAUSED,
    TaskStatus.WAITING_FOR_APPROVAL,
})

# The only statuses a follow-up or a retry may transition from. Used as the
# predicate of the compare-and-swap, so it is the single definition of "this
# task is settled" rather than a literal repeated at each call site.
REQUEUEABLE_STATUSES = frozenset({
    TaskStatus.COMPLETED,
    TaskStatus.PARTIAL,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
})

# A resume is only meaningful from a task the user is waiting on.
RESUMABLE_STATUSES = frozenset({
    TaskStatus.PAUSED,
    TaskStatus.WAITING_FOR_APPROVAL,
})

# A task in one of these has already settled. Cleanup paths must never repaint
# one, and never report a failure the user did not see.
TERMINAL_TASK_STATUSES = frozenset({
    TaskStatus.COMPLETED,
    TaskStatus.PARTIAL,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
})


def browser_scope_from_session(owned: Any, *, now: Any = None) -> dict[str, Any]:
    """Project a persisted browser grant into graph state, or an empty scope.

    Pure and server-derived by construction: origins, capabilities and tabs are
    read from the stored row, never from the inbound websocket message. A caller
    that supplies only a session id therefore cannot widen its own browser
    permissions, and an id it does not own resolves to no scope at all.

    Kept at module scope (rather than inside the socket handler) so the trust
    boundary is directly testable.
    """
    if owned is None:
        return {
            "browser_session_id": None,
            "browser_session_mode": "task_workspace",
            "browser_session_authorized": False,
            "browser_session_origins": [],
            "browser_session_capabilities": [],
            "browser_session_tab_id": None,
            "browser_session_tab_ids": [],
        }
    moment = now or utcnow()
    lapsed = bool(
        (owned.authorization_expires_at and owned.authorization_expires_at < moment)
        or (owned.lease_expires_at and owned.lease_expires_at < moment)
    )
    authorized = owned.status in AUTHORIZED_SESSION_STATUSES and not lapsed
    return {
        "browser_session_id": str(owned.id),
        "browser_session_mode": str(getattr(owned, "mode", None) or "task_workspace"),
        "browser_session_authorized": authorized,
        "browser_session_origins": list(owned.requested_origins or []) if authorized else [],
        "browser_session_capabilities": list(owned.granted_capabilities or []) if authorized else [],
        "browser_session_tab_id": owned.primary_tab_id if authorized else None,
        "browser_session_tab_ids": list(owned.tab_ids or []) if authorized else [],
    }


def json_safe(value: Any) -> Any:
    """Coerce graph/tool output into JSON that can be stored and streamed."""
    return json.loads(json.dumps(value, default=str))


def continuation_prompt(task: TaskThread, follow_up: str) -> str:
    """Build planner context only â€” never use this string as the executable goal.

    Deterministic planners match against ``state["task"]``. If the full transcript
    is stuffed into ``task``, patterns like "what's the current time" from an
    earlier turn hijack a later request such as "summarise https://...".
    """
    transcript = conversation_for(task)[-12:]
    formatted = "\n".join(
        f"{'User' if turn.get('role') == 'user' else 'Synth'}: {turn.get('content', '')}"
        for turn in transcript
    )
    return (
        "Prior conversation (context only â€” the executable goal is the latest follow-up):\n"
        f"{formatted}"
    )


def latest_user_goal(follow_up: str) -> str:
    """The only string deterministic planners and the executor should treat as the goal."""
    return str(follow_up or "").strip()


def original_task_goal(task: TaskThread) -> str:
    """Recover the immutable task intent, including legacy overwritten prompts."""
    conversation = conversation_for(task)
    first_user_turn = next(
        (
            str(turn.get("content") or "").strip()
            for turn in conversation
            if isinstance(turn, dict) and turn.get("role") == "user" and str(turn.get("content") or "").strip()
        ),
        "",
    )
    return first_user_turn or str(task.prompt or "").strip()


async def get_owned_task(
    session: AsyncSession,
    external_thread_id: str,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> Optional[TaskThread]:
    return await session.scalar(
        select(TaskThread).where(
            TaskThread.external_thread_id == external_thread_id,
            TaskThread.user_id == user_id,
            TaskThread.organization_id == organization_id,
        )
    )


def advisory_lock_key(identifier: uuid.UUID) -> int:
    """Reduce a UUID to a value `pg_advisory_xact_lock(bigint)` will accept.

    The previous expression, `int.from_bytes(identifier.bytes, "big",
    signed=True)`, produces a signed 128-bit integer. Postgres takes a bigint,
    so any UUID whose most significant bit is set -- roughly half of all UUIDs,
    and half of all v4 UUIDs -- raised
    `invalid input for query argument: value out of int64 range` in the middle
    of record_event. A task in that half recorded no events at all, and the
    DataError aborted the write rather than degrading it.

    Taking the low 64 bits, read as signed, is also what a lock key wants: the
    lock only has to serialise writers for the same row, and the collision cost
    of folding 128 bits onto 64 is one extra serialisation between two unrelated
    tasks, which is unmeasurable next to the event write itself. The low half
    must be read signed, not unsigned -- read unsigned it still reaches 2**64-1
    and overflows the same column.
    """
    return int.from_bytes(identifier.bytes[8:], "big", signed=True)


async def record_event(session: AsyncSession, task: TaskThread, event: dict[str, Any]) -> None:
    metadata = dict(task.execution_metadata or {})
    # Reconnects and worker retries can replay an event. Producers that provide
    # an identity are deduplicated before any task/run side effect is applied.
    if not should_apply_event(metadata, event):
        return
    metadata["last_event_id"] = event_identity(event) or metadata.get("last_event_id")
    target_phase = phase_for_event(str(event.get("type", "")), failure_retryable=bool(event.get("retryable")), status=event.get("status"))
    if target_phase is not None:
        transition = validate_transition(task.status, target_phase, str(event.get("type", "")))
        metadata["last_transition"] = {
            "from": transition.previous.value,
            "to": transition.current.value,
            "event": transition.event_type,
            "accepted": transition.accepted,
            "reason": transition.reason,
        }
        # Invalid late events remain in the audit log, but cannot repaint a
        # terminal or newer state. This is the server-side counterpart to the
        # frontend's stale-socket guards.
        if not transition.accepted:
            metadata["ignored_transition_count"] = int(metadata.get("ignored_transition_count", 0)) + 1
    # Every progress event pushes the dispatch lease out, so a healthy task
    # keeps moving its own deadline and only genuinely abandoned work expires.
    refresh_task_lease(task)
    # Allocate the event sequence under a per-task advisory lock. Reading
    # max(sequence) and inserting without one lets two concurrent writers pick
    # the same value, and uq_task_event_sequence then turns that race into an
    # IntegrityError that costs a whole task's event stream rather than merely
    # duplicating a number. The lock is transaction-scoped, so it is released on
    # commit and cannot be leaked by a crash. An unsaved task has no id and
    # nothing to contend over, so it skips the lock.
    if task.id is not None:
        await session.execute(
            select(func.pg_advisory_xact_lock(advisory_lock_key(task.id)))
        )
    last_sequence = await session.scalar(select(func.max(TaskEvent.sequence)).where(TaskEvent.task_id == task.id))
    event_record = TaskEvent(
        task_id=task.id,
        sequence=(last_sequence or 0) + 1,
        event_type=str(event.get("type", "unknown")),
        payload=json_safe(event),
        # An event the producer already stamped wins: a worker replaying a
        # queued task belongs to the trace that originally ran it, not to
        # whatever request happened to pick it up.
        trace_id=str(event.get("trace_id") or "") or current_trace_id() or None,
    )
    session.add(event_record)
    await session.flush()
    session.add(OutboxEvent(
        event_id=event_record.id,
        task_id=task.id,
        topic=f"task.{event_record.event_type}",
        payload=json_safe(event),
        available_at=utcnow(),
    ))
    artifact_items = event.get("artifacts") if isinstance(event.get("artifacts"), list) else []
    if isinstance(event.get("artifact"), dict):
        artifact_items = [*artifact_items, event["artifact"]]
    durable_artifacts = [item for item in (metadata.get("artifacts") or []) if isinstance(item, dict)]
    for artifact in artifact_items:
        if not isinstance(artifact, dict) or not artifact.get("attachment_id"):
            continue
        artifact_id = str(artifact["attachment_id"])
        durable_artifacts = [item for item in durable_artifacts if str(item.get("attachment_id") or "") != artifact_id]
        durable_artifacts.append(artifact)
    if durable_artifacts:
        metadata["artifacts"] = durable_artifacts[-20:]
    active_run = None
    active_run_id = metadata.get("active_run_id")
    if active_run_id:
        active_run = await session.get(AgentRun, active_run_id)
    if event.get("type") == "task_start" and active_run is None:
        active_run = await start_run(session, task, intent={"goal": task.prompt})
        metadata["active_run_id"] = str(active_run.id)
    if active_run is not None:
        if event.get("type") == "planner_output":
            plan = event.get("content")
            if isinstance(plan, list):
                active_run.plan = json_safe(plan)
        if event.get("type") in {"task_complete", "task_error"}:
            active_run.status = str(event.get("status") or ("completed" if event.get("type") == "task_complete" else "failed"))
            active_run.completed_at = utcnow()
            if event.get("type") == "task_error":
                active_run.error = json_safe({"message": event.get("message"), "error_code": event.get("error_code")})
        for artifact in artifact_items:
            if not isinstance(artifact, dict) or not artifact.get("attachment_id"):
                continue
            await add_artifact(
                session,
                active_run,
                filename=str(artifact.get("name") or "onsite48-artifact"),
                media_type=str(artifact.get("content_type") or "application/octet-stream"),
                storage_key=str(artifact.get("attachment_id")),
                checksum=str(artifact.get("sha256") or ""),
                provenance=json_safe(artifact.get("provenance") or {
                    "source_title": artifact.get("source_title"),
                    "artifact_type": artifact.get("artifact_type"),
                }),
            )
        content = event.get("content")
        if isinstance(content, dict) and content.get("tool") in {"gmail_create_draft", "gmail_send_message"}:
            tool_data = content.get("tool_data") if isinstance(content.get("tool_data"), dict) else {}
            if tool_data:
                artifacts = [item for item in (metadata.get("email_artifacts") or []) if isinstance(item, dict)]
                artifact = {
                    "type": "gmail_draft" if content.get("tool") == "gmail_create_draft" else "gmail_message",
                    "draft_id": tool_data.get("draft_id"),
                    "message_id": tool_data.get("message_id"),
                    "to": (content.get("tool_args") or {}).get("to"),
                    "subject": (content.get("tool_args") or {}).get("subject"),
                    "body": (content.get("tool_args") or {}).get("body"),
                    "status": tool_data.get("status"),
                }
                artifacts.append({key: value for key, value in artifact.items() if value is not None})
                metadata["email_artifacts"] = artifacts[-20:]
                # Emit a durable, replayable artifact event so the UI can render it on
                # refetch even if it missed the original step event.
                session.add(TaskEvent(
                    task_id=task.id,
                    sequence=(await session.scalar(
                        select(func.max(TaskEvent.sequence)).where(TaskEvent.task_id == task.id)
                    ) or 0) + 1,
                    event_type="artifact_created",
                    payload=json_safe({
                        "type": "artifact_created",
                        "artifact": artifact,
                        "thread_id": task.external_thread_id,
                        "timestamp": utcnow().isoformat(),
                    }),
                ))
        if isinstance(content, dict) and content.get("idempotency_key") and content.get("tool"):
            arguments = json_safe(content.get("tool_args") or {})
            key = str(content["idempotency_key"])
            existing_attempt = await session.scalar(select(AgentStepAttempt).where(AgentStepAttempt.run_id == active_run.id, AgentStepAttempt.idempotency_key == key))
            if existing_attempt is None:
                session.add(AgentStepAttempt(
                    run_id=active_run.id,
                    step_id=str(content.get("step_id") or content.get("step_index") or "step"),
                    attempt_number=int(content.get("attempts", 1) or 1),
                    idempotency_key=key,
                    tool_name=str(content["tool"]),
                    arguments=arguments,
                    status="completed" if not content.get("failure") else "failed",
                    result=json_safe(content),
                    error=json_safe(content.get("failure")) if content.get("failure") else None,
                    completed_at=utcnow(),
                ))
        research_payload = content.get("wide_research") if isinstance(content, dict) else None
        if not isinstance(research_payload, dict) and isinstance(content, dict):
            research_payload = (content.get("tool_data") or {}).get("wide_research")
        if isinstance(research_payload, dict):
            for branch in research_payload.get("branches", []):
                if not isinstance(branch, dict):
                    continue
                for item in branch.get("evidence", []):
                    if not isinstance(item, dict) or not item.get("source_url"):
                        continue
                    session.add(AgentEvidence(
                        run_id=active_run.id,
                        step_id=str(content.get("step_id") or "wide-research"),
                        source_url=str(item["source_url"]),
                        title=str(branch.get("facet") or "Wide Research source"),
                        content=str(branch.get("summary") or branch.get("raw_evidence") or "")[:12000],
                        evidence_metadata=json_safe({"agent_id": branch.get("agent_id"), "facet": branch.get("facet")}),
                    ))
    if metadata != (task.execution_metadata or {}):
        task.execution_metadata = metadata
    new_status = status_from_event(event)
    # A pause is an explicit user decision. A late task_complete/task_error
    # emitted by an in-flight stream must be retained as an audit event but
    # must not repaint the durable sidebar task as failed or completed.
    preserve_paused_status = task.status == TaskStatus.PAUSED and new_status in {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }
    if new_status and not preserve_paused_status and (target_phase is None or metadata.get("last_transition", {}).get("accepted", True)):
        task.status = new_status
        if new_status and not preserve_paused_status and new_status == TaskStatus.RUNNING and task.started_at is None:
            task.started_at = utcnow()
        if new_status and not preserve_paused_status and new_status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            task.completed_at = utcnow()
    if event.get("final_answer"):
        task.final_answer = str(event["final_answer"])
        if event.get("type") == "task_complete":
            append_conversation_turn(task, "assistant", task.final_answer)
    if event.get("cost_metrics"):
        task.cost_metrics = json_safe(event["cost_metrics"])
    if event.get("checkpoint") or event.get("type") in {"planner_output", "executor_output", "verifier_output", "website_preview", "artifact_created", "needs_confirmation", "needs_user_intervention", "needs_clarification", "task_complete", "task_error"}:
        metadata["checkpoint"] = json_safe(event.get("checkpoint") or {
            "event_type": event.get("type"),
            "step_index": event.get("step_index"),
            "status": str(task.status),
        })
        metadata["lifecycle"] = lifecycle_snapshot(task.status, metadata)
    if event.get("type") == "needs_confirmation":
        approval = ToolApproval(
            task_id=task.id,
            tool_name=str(event.get("tool_name", "unknown_tool")),
            step_index=int(event.get("step_index", 0)),
            arguments=json_safe(event.get("tool_args", {})),
        )
        session.add(approval)
    await session.flush()


async def mark_approval(
    session: AsyncSession,
    task: TaskThread,
    tool_name: str,
    step_index: int,
    decision: bool,
    user_id: uuid.UUID,
    tool_args: dict[str, Any] | None = None,
) -> ToolApproval:
    """Record an operator approval decision.

    Primary lookup is by ``(task_id, tool_name, step_index)``. When a replan
    reorders steps, the same external action may carry a different index â€”
    so we fall back to matching on the serialized tool arguments, which is
    the stable identity used by ``confirmation_key`` in the executor.
    """
    approval = await session.scalar(
        select(ToolApproval)
        .where(
            ToolApproval.task_id == task.id,
            ToolApproval.tool_name == tool_name,
            ToolApproval.step_index == step_index,
            ToolApproval.decision.is_(None),
        )
        .order_by(ToolApproval.created_at.desc())
    )

    if approval is None and tool_args is not None:
        # Replan may have shifted step positions. Match on the action
        # fingerprint instead so the same approved action still resolves.
        import json as _json
        fingerprint = _json.dumps(tool_args, sort_keys=True, separators=(",", ":"), default=str)
        approval = await session.scalar(
            select(ToolApproval)
            .where(
                ToolApproval.task_id == task.id,
                ToolApproval.tool_name == tool_name,
                ToolApproval.arguments.is_(tool_args),
                ToolApproval.decision.is_(None),
            )
            .order_by(ToolApproval.created_at.desc())
        )
        if approval is not None:
            # Reconcile the stored step_index with the caller's current view
            # so downstream UI and logging stay consistent.
            approval.step_index = step_index
            stored_args = dict(approval.arguments or {})
            stored_args["_fingerprint"] = fingerprint
            approval.arguments = stored_args

    if approval is None:
        raise ValueError("No pending approval exists for that tool action.")
    approval.decision = decision
    approval.decided_by_user_id = user_id
    approval.decided_at = utcnow()
    task.status = TaskStatus.RUNNING if decision else TaskStatus.WAITING_FOR_APPROVAL
    await session.flush()
    return approval


async def secure_websocket_endpoint(
    websocket: WebSocket,
    *,
    user: User,
    membership: OrganizationMember,
    session_factory: async_sessionmaker[AsyncSession],
    checkpointer: Any,
    model_router: Any,
    browser_service: Any,
    tool_registry: Any,
    domain_router: Any,
) -> None:
    """Run the authenticated agent protocol for a single browser connection."""
    offered_protocols = [value.strip() for value in websocket.headers.get("sec-websocket-protocol", "").split(",") if value.strip()]
    await websocket.accept(subprotocol="onsite48.v1" if "onsite48.v1" in offered_protocols else None)
    user_id = user.id
    organization_id = membership.organization_id
    client_id = str(uuid.uuid4())
    # A socket carries the whole lifetime of a task, so the trace is bound once
    # here rather than per event. Binding it per event would make the steps of
    # one task look like unrelated work in the log.
    trace_id = bind_request_trace(websocket.headers)
    logger.info(
        "realtime.connected",
        client_id=client_id,
        user_id=str(user_id),
        organization_id=str(organization_id),
        trace_id=trace_id,
    )

    # The task currently in flight on this socket. It is the only state the
    # cleanup path below is allowed to touch, so a second socket, a reconnect,
    # or a sweeper cannot race it. Cleared on every completed action so a
    # follow-up that supersedes the current task does not leave a stale row.
    current_task: Optional[TaskThread] = None
    current_thread_id: Optional[str] = None
    terminal_event_id: Optional[str] = None

    # ─── Server-side heartbeat ────────────────────────────────────────
    # Send periodic pings to keep the connection alive through proxies,
    # load balancers, and network devices that drop idle connections.
    # Client also pings every 10s; server pings every 15s provides bidirectional
    # keepalive so a network blip during a long model call (30-60s) doesn't
    # silently kill the socket.
    heartbeat_task: Optional[asyncio.Task] = None
    heartbeat_interval = int(os.getenv("WEBSOCKET_HEARTBEAT_INTERVAL_SECONDS", "15"))

    async def _heartbeat() -> None:
        try:
            while True:
                await asyncio.sleep(heartbeat_interval)
                try:
                    await websocket.send_json({"type": "ping", "server": True, "timestamp": utcnow().isoformat()})
                except Exception:
                    # Connection closed, stop heartbeat
                    break
        except asyncio.CancelledError:
            pass
        except Exception:
            # Don't log heartbeat failures; they're expected on close
            pass

    heartbeat_task = asyncio.create_task(_heartbeat())

    def _track(task: TaskThread) -> None:
        """Record which task the cleanup path is allowed to touch.

        A follow-up or a retry supersedes the previous task, so the terminal
        event identity is reset with it. Keeping the old id would attach the
        next cleanup to the *previous* task's error event, and the state machine
        would then deduplicate the real failure as a replay.
        """
        nonlocal current_task, current_thread_id, terminal_event_id
        previous_id = current_task.id if current_task is not None else None
        current_task = task
        current_thread_id = str(task.external_thread_id)
        if previous_id != task.id:
            terminal_event_id = None

    async def _fail_in_flight_task(
        exc: Exception | None,
        *,
        retryable: bool,
        error_code: str = "websocket_execution_failed",
        message: Optional[str] = None,
    ) -> None:
        """Mark the in-flight task failed and release its lease.

        Idempotent: a task already terminal is left alone, and any exception
        raised here is swallowed so it cannot mask the original failure that
        triggered the cleanup.

        ``exc`` is optional because the two callers have different evidence: a
        disconnect has no server-side exception, while an outer handler failure
        has one. Either way the durable outcome is the same, so the message says
        what the user can do next rather than blaming a specific cause.
        """
        nonlocal current_task, current_thread_id, terminal_event_id
        if current_task is None or current_thread_id is None:
            return
        task = current_task
        thread_id = current_thread_id
        default_message = (
            "The agent connection to Synth was interrupted before this task "
            "finished. Nothing was left half-done; you can run it again."
        )
        failure_message = message or default_message
        try:
            async with session_factory() as session:
                persisted = await session.get(TaskThread, task.id)
                if persisted is None:
                    return
                if persisted.status in TERMINAL_TASK_STATUSES:
                    # Already settled by a terminal event this socket carried.
                    # Failing it again would log a failure the user never saw.
                    return
                if persisted.status in RESUMABLE_STATUSES:
                    # An approval, clarification, or intervention pause is waiting
                    # for the user, not running work. A closed tab must not turn
                    # "come back and confirm" into a failure.
                    logger.info(
                        "realtime.resumable_pause_preserved",
                        client_id=client_id,
                        thread_id=thread_id,
                        task_id=str(task.id),
                        status=persisted.status.value,
                    )
                    return
                logger.warning(
                    "realtime.task_failed_on_socket_error",
                    client_id=client_id,
                    user_id=str(user_id),
                    thread_id=thread_id,
                    task_id=str(task.id),
                    status=persisted.status.value,
                    error_code=error_code,
                    error_type=type(exc).__name__ if exc is not None else None,
                    error=str(exc) if exc is not None else None,
                    retryable=retryable,
                )
                # `fail_task_terminal` returns True when it actually failed the
                # task and False when the row was already terminal. Reading it
                # the other way round released the lease on the paths where the
                # task needed no cleanup and skipped it on exactly the path
                # that had just stranded a RUNNING row -- leaving the lease to
                # expire on a task nobody would ever retry.
                failed_now = await fail_task_terminal(
                    session,
                    persisted,
                    error_code=error_code,
                    message=failure_message,
                    retryable=retryable,
                    trace_id=trace_id,
                    thread_id=thread_id,
                    error_event_id=terminal_event_id,
                )
                if not failed_now:
                    return
                await release_task_lease_by_id(session_factory, persisted.id)
                await session.commit()
        except Exception as cleanup_exc:  # pragma: no cover - defensive
            logger.error(
                "realtime.cleanup_failed",
                client_id=client_id,
                thread_id=thread_id,
                task_id=str(task.id),
                error_type=type(cleanup_exc).__name__,
                error=str(cleanup_exc),
            )

    async def send(event: dict[str, Any], task: Optional[TaskThread] = None) -> None:
        # Every event carries the trace so the frontend can show one identifier
        # that resolves to the whole task in the server log. A user reporting
        # "it has been stuck for ten minutes" has no way to produce a timestamp
        # match by hand.
        safe_event = json_safe(
            {
                **event,
                "trace_id": str(event.get("trace_id") or "") or trace_id,
                "timestamp": event.get("timestamp", utcnow().isoformat()),
            }
        )
        # Streaming deltas are presentation-only. Persisting every token would
        # add database work to the fast path and create an unusable event log.
        if safe_event.pop("transient", False):
            await websocket.send_json(safe_event)
            return
        client_event = safe_event
        if task is not None:
            # Track the in-flight task so the cleanup path can fail it if this
            # socket dies. A follow-up that supersedes the current task clears
            # the reference first, so a late terminal event for the old task
            # cannot be mistaken for the live one.
            _track(task)
            if safe_event.get("type") in {"task_complete", "task_error"}:
                terminal_event_id = str(safe_event.get("event_id") or uuid.uuid4())
            async with session_factory() as session:
                persisted_task = await session.get(TaskThread, task.id)
                if persisted_task:
                    durable_event, client_event = prepare_report_delivery(
                        safe_event,
                        organization_id=str(organization_id),
                        user_id=str(user_id),
                        title=persisted_task.title,
                    )
                    if safe_event.get("step_index") is not None or safe_event.get("tool_name") or safe_event.get("type") in {"task_start", "task_complete", "task_error", "needs_confirmation", "needs_clarification", "user_intervention_required"}:
                        get_eval_viewer().record(str(persisted_task.external_thread_id), durable_event)
                    await record_event(session, persisted_task, durable_event)
                    await session.commit()
        await websocket.send_json(client_event)

    async def load_task(thread_id: str) -> Optional[TaskThread]:
        async with session_factory() as session:
            return await get_owned_task(session, thread_id, user_id, organization_id)

    async def load_owned_browser_session(raw_session_id: Any) -> Optional[BrowserSession]:
        """Resolve a client-supplied session id to one this socket's user owns.

        The id is treated as an untrusted reference: it selects a persisted row
        or nothing. No part of the granted scope is ever read from the client
        message, so a caller cannot assert its own browser permissions.
        """
        if not raw_session_id:
            return None
        try:
            session_id = uuid.UUID(str(raw_session_id))
        except (TypeError, ValueError):
            return None
        async with session_factory() as session:
            owned = await session.scalar(select(BrowserSession).where(
                BrowserSession.id == session_id,
                BrowserSession.user_id == user_id,
                BrowserSession.organization_id == organization_id,
            ))
        if owned is None:
            logger.warning(
                "realtime.browser_session_not_owned",
                requested=str(raw_session_id),
                user_id=str(user_id),
                organization_id=str(organization_id),
            )
        return owned

    try:
        while True:
            try:
                data = await websocket.receive_json()
            except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
                # A malformed frame is a client error, not a server failure.
                # Failing the in-flight task here would abort real work because
                # a browser extension sent one bad message.
                logger.warning(
                    "realtime.malformed_frame",
                    client_id=client_id,
                    error_type=type(exc).__name__,
                )
                try:
                    await websocket.send_json({
                        "type": "error",
                        "message": "That message could not be read. Please try again.",
                        "timestamp": utcnow().isoformat(),
                    })
                except Exception:
                    pass
                continue
            if not isinstance(data, dict):
                await send({"type": "error", "message": "Unsupported message format."})
                continue
            action = data.get("action")
            if action == "ping":
                await send({"type": "pong"})
                continue

            if action == "run_task":
                prompt = str(data.get("message", "")).strip()
                if not prompt:
                    await send({"type": "error", "message": "Please enter a task before running Synth."})
                    continue
                research_depth = data.get("research_depth")
                if research_depth not in (None, "quick", "standard", "deep"):
                    research_depth = None
                raw_attachment_ids = data.get("attachments", [])
                attachment_ids = raw_attachment_ids if isinstance(raw_attachment_ids, list) and all(isinstance(item, str) for item in raw_attachment_ids) else []
                try:
                    attachments, attachment_context = attachment_store.task_context(
                        organization_id=str(organization_id),
                        user_id=str(user_id),
                        attachment_ids=attachment_ids,
                    )
                except HTTPException as exc:
                    await send({"type": "error", "message": str(exc.detail)})
                    continue
                execution_prompt = prompt if not attachment_context else f"{prompt}\n\n{attachment_context}"
                thread_id = str(uuid.uuid4())
                async with session_factory() as session:
                    task = TaskThread(
                        external_thread_id=thread_id,
                        user_id=user_id,
                        organization_id=organization_id,
                        title=concise_task_title(prompt),
                        prompt=prompt,
                        domain=str(data.get("domain", "general"))[:64],
                        status=TaskStatus.QUEUED,
                        execution_metadata={
                            **ensure_task_presentation({"conversation": [{"turn_id": str(uuid.uuid4()), "role": "user", "content": prompt, "at": utcnow().isoformat()}]}, prompt, str(data.get("domain", "general"))),
                            "turn_count": 1,
                            "attachments": attachments,
                        },
                    )
                    session.add(task)
                    await session.commit()
                    await session.refresh(task)
                owned_browser_session = await load_owned_browser_session(data.get("browser_session_id"))
                if owned_browser_session is not None:
                    await browser_live_registry.subscribe(owned_browser_session.id, websocket)
                    # The session was created before this task existed, so its
                    # task link is empty. Stamp it now: authorization records
                    # and session events key off the task row, and this is the
                    # one place the session is bound to the task it executes.
                    async with session_factory() as link_session:
                        row = await link_session.get(BrowserSession, owned_browser_session.id)
                        if row is not None and row.task_id is None:
                            row.task_id = task.id
                            await link_session.commit()
                fast_route = select_fast_route(
                    prompt,
                    has_attachments=bool(attachment_ids),
                    is_follow_up=False,
                )
                if fast_route is not None:
                    await send(
                        {
                            "type": "task_start",
                            "task_id": str(task.id),
                            "thread_id": thread_id,
                            "message": prompt,
                            "response_mode": fast_route.mode,
                        },
                        task,
                    )
                    async for event in stream_fast_answer(
                        message=prompt,
                        decision=fast_route,
                        model_router=model_router,
                        tool_registry=tool_registry,
                    ):
                        await send({**event, "thread_id": thread_id}, task)
                    continue
                # Every request uses the durable planner/executor stream. Simple
                # requests still complete cheaply because the planner can emit a
                # deterministic one-step plan (for example current_time); no
                # separate model-only fast lane can bypass tools or lifecycle
                # persistence.

                async for event in stream_task_events(
                    execution_prompt,
                    str(user_id),
                    str(organization_id),
                    thread_id,
                    checkpointer,
                    model_router,
                    tool_registry,
                    domain_router,
                    browser_service,
                    extra_state={
                        "task_metadata": task.execution_metadata or {},
                        "research_depth": research_depth,
                        **browser_scope_from_session(owned_browser_session),
                    },
                ):
                    outgoing_event = {**event, "thread_id": thread_id}
                    if event.get("type") == "task_start":
                        outgoing_event["message"] = prompt
                        outgoing_event["response_mode"] = "autonomous"
                    await send(outgoing_event, task)
                continue

            thread_id = str(data.get("thread_id", "")).strip()
            if not thread_id:
                await send({"type": "error", "message": "A task identifier is required."})
                continue
            task = await load_task(thread_id)
            if task is None:
                await send({"type": "error", "message": "Task not found or access denied."})
                continue

            if action == "continue_task":
                follow_up = str(data.get("message", "")).strip()
                if not follow_up:
                    await send({"type": "error", "message": "Please enter a follow-up message."}, task)
                    continue
                research_depth = data.get("research_depth")
                if research_depth not in (None, "quick", "standard", "deep"):
                    research_depth = None
                owned_browser_session = await load_owned_browser_session(data.get("browser_session_id"))
                if owned_browser_session is not None:
                    await browser_live_registry.subscribe(owned_browser_session.id, websocket)
                    # A chat-created session predates the task row, so its
                    # task link can still be empty on a follow-up. Bind it to
                    # the task it is now executing; authorization records and
                    # session events key off the task row.
                    async with session_factory() as link_session:
                        row = await link_session.get(BrowserSession, owned_browser_session.id)
                        if row is not None and row.task_id is None:
                            row.task_id = task.id
                            await link_session.commit()
                if task.status in ACTIVE_TASK_STATUSES:
                    await send({"type": "error", "message": "Wait for the current task to finish before sending a follow-up."}, task)
                    continue
                raw_attachment_ids = data.get("attachments", [])
                attachment_ids = raw_attachment_ids if isinstance(raw_attachment_ids, list) and all(isinstance(item, str) for item in raw_attachment_ids) else []
                try:
                    attachments, attachment_context = attachment_store.task_context(
                        organization_id=str(organization_id),
                        user_id=str(user_id),
                        attachment_ids=attachment_ids,
                    )
                except HTTPException as exc:
                    await send({"type": "error", "message": str(exc.detail)}, task)
                    continue
                async with session_factory() as session:
                    current_task = await session.get(TaskThread, task.id)
                    assert current_task is not None
                    append_conversation_turn(current_task, "user", follow_up)
                    metadata = dict(current_task.execution_metadata or {})
                    metadata["attachments"] = [*(metadata.get("attachments", []) or []), *attachments][-20:]
                    current_task.execution_metadata = metadata
                    # Keep task.prompt immutable: it is the durable original
                    # intent used to replay a failed task. The latest follow-up
                    # remains in the conversation and becomes the current goal
                    # only for this execution.
                    await session.commit()
                # Compare-and-swap: the staleness check above read `task`, not
                # this row, so a task cancelled or completed since then must not
                # be silently requeued.
                try:
                    async with session_factory() as session:
                        current_task = await transition_status(
                            session,
                            task.id,
                            expected=REQUEUEABLE_STATUSES,
                            target=TaskStatus.QUEUED,
                            values={"completed_at": None},
                        )
                        conversation_context = continuation_prompt(current_task, follow_up)
                        email_artifacts = list(
                            (current_task.execution_metadata or {}).get("email_artifacts") or []
                        )
                except StatusConflict:
                    logger.info("realtime.follow_up_lost_race", thread_id=thread_id)
                    await send(
                        {"type": "error", "message": "This task changed state; the follow-up was not queued."},
                        task,
                    )
                    continue
                if attachment_context:
                    conversation_context = f"{conversation_context}\n\n{attachment_context}"
                # Executable goal must be the raw follow-up only. Passing the
                # full transcript as task caused deterministic shortcuts (e.g.
                # current_time) from earlier turns to fire on unrelated asks.
                goal = latest_user_goal(follow_up)
                async for event in stream_task_events(
                    goal,
                    str(user_id),
                    str(organization_id),
                    thread_id,
                    checkpointer,
                    model_router,
                    tool_registry,
                    domain_router,
                    browser_service,
                    extra_state={
                        "task_metadata": current_task.execution_metadata or {},
                        "conversation_context": conversation_context,
                        "is_follow_up": True,
                        "email_artifacts": email_artifacts,
                        "research_depth": research_depth,
                        **browser_scope_from_session(owned_browser_session),
                    },
                    reset_checkpoint=True,
                ):
                    outgoing_event = {**event, "thread_id": thread_id, "is_follow_up": True}
                    if event.get("type") == "task_start":
                        outgoing_event["message"] = follow_up
                    await send(outgoing_event, task)
                continue

            if action == "retry_task":
                if task.status in ACTIVE_TASK_STATUSES:
                    await send({"type": "error", "message": "Wait for the current task to finish before retrying it."}, task)
                    continue
                research_depth = data.get("research_depth")
                if research_depth not in (None, "quick", "standard", "deep"):
                    # Fall back to the original task's research_depth if present
                    research_depth = (task.execution_metadata or {}).get("research_depth")
                    if research_depth not in (None, "quick", "standard", "deep"):
                        research_depth = None
                retry_goal = original_task_goal(task)
                if not retry_goal:
                    await send({"type": "error", "message": "This task has no recoverable original request. Please submit it again."}, task)
                    continue
                try:
                    async with session_factory() as session:
                        current_task = await transition_status(
                            session,
                            task.id,
                            expected=REQUEUEABLE_STATUSES,
                            target=TaskStatus.QUEUED,
                            values={"final_answer": None, "completed_at": None},
                        )
                        retry_context = continuation_prompt(current_task, retry_goal)
                except StatusConflict:
                    logger.info("realtime.retry_lost_race", thread_id=thread_id)
                    await send(
                        {"type": "error", "message": "This task changed state; the retry was not queued."},
                        task,
                    )
                    continue
                async for event in stream_task_events(
                    retry_goal,
                    str(user_id),
                    str(organization_id),
                    thread_id,
                    checkpointer,
                    model_router,
                    tool_registry,
                    domain_router,
                    browser_service,
                    extra_state={
                        "task_metadata": current_task.execution_metadata or {},
                        "conversation_context": retry_context,
                        "is_retry": True,
                        "research_depth": research_depth,
                    },
                    reset_checkpoint=True,
                ):
                    await send({**event, "thread_id": thread_id, "is_retry": True}, task)
                continue

            if action in {"confirm_tool", "reject_tool", "confirm_read_only_for_task"}:
                tool_name = str(data.get("tool_name", "")).strip()
                step_index = data.get("step_index")
                if not tool_name or not isinstance(step_index, int):
                    await send({"type": "error", "message": "Tool name and step index are required."}, task)
                    continue
                decision = action in {"confirm_tool", "confirm_read_only_for_task"}
                if action == "confirm_read_only_for_task" and tool_name not in SAFE_TASK_SCOPED_TOOLS:
                    await send({"type": "error", "message": "Only read-only Onsite48 Bridge actions can receive task-scoped approval."}, task)
                    continue
                try:
                    async with session_factory() as session:
                        current_task = await session.get(TaskThread, task.id)
                        assert current_task is not None
                        approval = await mark_approval(session, current_task, tool_name, step_index, decision, user_id)
                        if action == "confirm_read_only_for_task":
                            metadata = dict(current_task.execution_metadata or {})
                            metadata["task_scoped_read_only_approval"] = {
                                "granted_at": utcnow().isoformat(),
                                "granted_by_user_id": str(user_id),
                                "source_tool": tool_name,
                            }
                            current_task.execution_metadata = metadata
                        await session.commit()
                except ValueError as exc:
                    await send({"type": "error", "message": str(exc)}, task)
                    continue
                extra_state = (
                    {
                        "confirmed_actions": {
                            confirmation_key(thread_id, step_index, tool_name, dict(approval.arguments or {})): True,
                        },
                        **({"task_scoped_read_only_approval": True} if action == "confirm_read_only_for_task" else {}),
                    }
                    if decision
                    else {
                        "skipped_steps": {step_index: True},
                        "rejected_tool_call": {
                            "tool_name": tool_name,
                            "tool_args": dict(approval.arguments or {}),
                        },
                    }
                )
                async for event in stream_task_events(
                    task.prompt,
                    str(user_id),
                    str(organization_id),
                    thread_id,
                    checkpointer,
                    model_router,
                    tool_registry,
                    domain_router,
                    browser_service,
                    extra_state=extra_state,
                    resume_from_checkpoint=True,
                ):
                    await send({**event, "thread_id": thread_id}, task)
                continue

            if action == "resume_task":
                # An optional answer to a clarification question.
                #
                # A clarification pause is the one pause where the user is
                # expected to *type something back*, so resuming without a
                # message would restart the same question. `continue_task`
                # cannot carry the answer instead: a clarification is persisted
                # as PAUSED (task_persistence.status_from_event), and PAUSED is
                # in ACTIVE_TASK_STATUSES, so a follow-up is rejected with
                # "wait for the current task to finish" -- which is correct for
                # an approval pause and wrong here.
                #
                # The answer is appended to the conversation, and the transcript
                # is passed as context only. The executable goal stays
                # `task.prompt`, exactly as before: resuming a clarified task must
                # re-enter the graph where it stopped, not re-plan the answer
                # text as if it were a fresh request.
                clarification_answer = str(data.get("message", "")).strip()
                paused_state = await get_paused_task_state(thread_id)
                if paused_state is None:
                    async with session_factory() as session:
                        current_task = await session.get(TaskThread, task.id)
                        if current_task is None or current_task.status not in RESUMABLE_STATUSES:
                            await send({"type": "error", "message": "This task is not currently paused."}, task)
                            continue
                        logger.warning("realtime.resume_task_fallback_to_db_status", thread_id=thread_id, status=current_task.status.value)
                # Compare-and-swap on the statuses a resume may follow. The
                # check above ran in a different session, so between it and this
                # write the task may have been cancelled or already resumed;
                # an unconditional assignment here would revive it.
                try:
                    async with session_factory() as session:
                        await transition_status(
                            session,
                            task.id,
                            expected=RESUMABLE_STATUSES,
                            target=TaskStatus.RUNNING,
                        )
                except StatusConflict:
                    logger.info("realtime.resume_task_lost_race", thread_id=thread_id)
                    await send(
                        {"type": "error", "message": "This task is no longer paused."},
                        task,
                    )
                    continue
                # Recorded only after the compare-and-swap succeeds, so a resume
                # that lost the race does not leave an answer in the transcript
                # for a task that never consumed it.
                research_depth = data.get("research_depth")
                if research_depth not in (None, "quick", "standard", "deep"):
                    research_depth = (task.execution_metadata or {}).get("research_depth")
                    if research_depth not in (None, "quick", "standard", "deep"):
                        research_depth = None
                resume_extra_state: dict[str, Any] = {
                    "task_metadata": task.execution_metadata or {},
                    "research_depth": research_depth,
                }
                if clarification_answer:
                    async with session_factory() as session:
                        current_task = await session.get(TaskThread, task.id)
                        if current_task is not None:
                            append_conversation_turn(current_task, "user", clarification_answer)
                            resume_extra_state["task_metadata"] = current_task.execution_metadata or {}
                            resume_extra_state["conversation_context"] = continuation_prompt(
                                current_task, clarification_answer
                            )
                            resume_extra_state["is_follow_up"] = True
                            await session.commit()
                if isinstance(data.get("modifications"), dict):
                    resume_extra_state["modifications"] = data["modifications"]
                async for event in stream_task_events(
                    task.prompt,
                    str(user_id),
                    str(organization_id),
                    thread_id,
                    checkpointer,
                    model_router,
                    tool_registry,
                    domain_router,
                    browser_service,
                    extra_state=resume_extra_state,
                    resume_from_checkpoint=True,
                ):
                    outgoing_event = {**event, "thread_id": thread_id}
                    if event.get("type") == "task_start":
                        outgoing_event["message"] = task.prompt
                    await send(outgoing_event, task)
                continue

            await send({"type": "error", "message": "Unsupported task action."}, task)
    except WebSocketDisconnect as exc:
        logger.info(
            "realtime.disconnected",
            client_id=client_id,
            user_id=str(user_id),
            close_code=exc.code,
        )
        # A client disconnect is not a server failure, but it must not leave a
        # task permanently marked as running or queued. Releasing the lease only
        # hands the task to the sweeper, which requeues it while attempts remain
        # -- so a tab that is never reopened would leave the row RUNNING until
        # the sweeper happens to run. The task is therefore failed terminally
        # here, with retryable set so the UI offers a rerun, and resumable pauses
        # are deliberately left alone: the user is expected to come back to an
        # approval, clarification, or intervention.
        await _fail_in_flight_task(
            exc,
            retryable=True,
            error_code="websocket_disconnected",
            message=(
                "The agent connection closed before this task finished, so it was "
                "stopped. Nothing was left half-done; you can run it again."
            ),
        )
    except Exception as exc:
        logger.exception("realtime.error", client_id=client_id, user_id=str(user_id), error=str(exc))
        # An unexpected exception in the outer handler is a server failure, not
        # a client disconnect. The in-flight task must be marked terminal failed
        # and its lease released so it does not sit in RUNNING forever and so a
        # reconnect or retry sees a durable failure rather than a stale lease.
        await _fail_in_flight_task(exc, retryable=False)
        try:
            await websocket.send_json({"type": "error", "message": "The agent connection encountered an unexpected error.", "timestamp": utcnow().isoformat()})
        except Exception:
            pass
    finally:
        # Stop server-side heartbeat
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except Exception:
                pass
        
        # Last-resort guarantee for the invariant "no task is left RUNNING":
        # whatever path left this handler, anything still in flight is failed
        # and its lease released. Both calls are idempotent, so the earlier
        # paths having already done the work costs nothing.
        await _fail_in_flight_task(
            None,
            retryable=True,
            error_code="websocket_closed",
            message=(
                "The agent connection ended before this task finished, so it was "
                "stopped. Nothing was left half-done; you can run it again."
            ),
        )
        try:
            await browser_live_registry.unsubscribe_websocket(websocket)
        except Exception as cleanup_exc:
            # Cleanup must not raise out of the endpoint: an unsubscribe failure
            # would mask the real disconnect reason from the access log.
            logger.warning(
                "realtime.browser_unsubscribe_failed",
                client_id=client_id,
                error_type=type(cleanup_exc).__name__,
                error=str(cleanup_exc),
            )
