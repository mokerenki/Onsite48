"""Enqueue and resume agent runs via the Phase 1–6 runtime loop."""
from __future__ import annotations

import os
from typing import Any

import structlog

from app.runtime.journal import journal as default_journal

logger = structlog.get_logger("onsite48.services.task_runner")

# In-memory fallback when DB is not configured (local / eval).
_RUNS: dict[str, dict[str, Any]] = {}


async def enqueue_run(
    *,
    run_id: str,
    task_id: str,
    goal: str,
    org_id: str | None = None,
    user_id: str | None = None,
    role: str = "loop",
    max_steps: int | None = None,
    max_cost_usd: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start an agent run. Uses AGENT_RUNTIME=loop by default."""
    _RUNS[run_id] = {
        "run_id": run_id,
        "task_id": task_id,
        "goal": goal,
        "status": "running",
        "org_id": org_id,
        "user_id": user_id,
        "role": role,
        "approval_pending": None,
        "metadata": metadata or {},
    }
    runtime = os.environ.get("AGENT_RUNTIME", "loop")
    if runtime != "loop":
        logger.warning("legacy_runtime_requested_but_deleted", runtime=runtime)
    try:
        from app.runtime import run as runtime_run

        role_config: dict[str, Any] = {"role": role}
        if max_steps is not None:
            role_config["max_steps"] = max_steps
        if max_cost_usd is not None:
            role_config["max_cost_usd"] = max_cost_usd
        if org_id:
            role_config["org_id"] = org_id
        if user_id:
            role_config["user_id"] = user_id

        result = await runtime_run(
            run_id,
            goal,
            role_config,
            user_id=user_id,
            task_id=task_id,
            org_id=org_id,
        )
        _RUNS[run_id].update(
            {
                "status": result.get("status", "unknown"),
                "summary": result.get("summary"),
                "cost_usd": result.get("cost_usd"),
                "step_count": result.get("step_count"),
                "approval_pending": result.get("approval_pending"),
                "finish_payload": result.get("finish_payload"),
                "error": result.get("error"),
            }
        )
        return _RUNS[run_id]
    except Exception as exc:
        logger.exception("run_failed", run_id=run_id, error=str(exc))
        _RUNS[run_id]["status"] = "failed"
        _RUNS[run_id]["error"] = {"message": str(exc)}
        return _RUNS[run_id]


async def get_run(run_id: str) -> dict[str, Any] | None:
    return _RUNS.get(run_id)


async def list_events(run_id: str) -> list:
    try:
        return await default_journal.list_events(run_id)
    except Exception:
        return []


async def resume_run(
    *,
    run_id: str,
    approved: bool,
    note: str | None = None,
) -> dict[str, Any]:
    """Resume a run that is waiting on approval."""
    state = _RUNS.get(run_id)
    if state is None:
        return {"error": "run not found"}
    state["approval_pending"] = None
    state["status"] = "running"
    # Re-enter the loop with approval decision in role_config.
    from app.runtime import run as runtime_run

    result = await runtime_run(
        run_id,
        state["goal"],
        {
            "role": state.get("role", "loop"),
            "approval_decision": {"approved": approved, "note": note},
            "resume": True,
        },
        user_id=state.get("user_id"),
        task_id=state.get("task_id"),
        org_id=state.get("org_id"),
    )
    state.update(
        {
            "status": result.get("status", "unknown"),
            "summary": result.get("summary"),
            "approval_pending": result.get("approval_pending"),
        }
    )
    return state
