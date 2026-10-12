# backend/app/api/history_api.py

from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Optional, List, Dict, Any
import structlog
from datetime import datetime, timezone, timedelta

from app.core.history_store import HistoryStore
from app.models import AgentRun, OrganizationMember, User, TaskThread, TaskEvent
from app.db import get_db_session
from app.core.security import get_current_user
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger("onsite48.api.history")
router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("/tasks")
async def list_tasks(
    user_id: str = Query("dashboard", description="User ID"),
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None, description="Filter by status"),
    from_date: Optional[str] = Query(None, description="ISO date string"),
    to_date: Optional[str] = Query(None, description="ISO date string"),
):
    """List tasks with filtering."""
    try:
        history_store = HistoryStore()
        await history_store.ensure_initialized()
        
        tasks = await history_store.list_user_tasks(
            user_id=user_id,
            limit=limit,
            status=status,
            from_date=from_date,
            to_date=to_date
        )
        
        return {
            "tasks": tasks,
            "total": len(tasks),
            "limit": limit,
        }
    except Exception as e:
        logger.exception("history.list_tasks_failed", error=str(e))
        raise HTTPException(500, f"Failed to list tasks: {str(e)}")


@router.get("/tasks/{thread_id}")
async def get_task_detail(thread_id: str):
    """Get full task detail including timeline."""
    try:
        history_store = HistoryStore()
        await history_store.ensure_initialized()
        
        task = await history_store.get_full_task(thread_id)
        if not task:
            raise HTTPException(404, f"Task {thread_id} not found")
        
        return task
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("history.get_task_failed", thread_id=thread_id, error=str(e))
        raise HTTPException(500, f"Failed to get task: {str(e)}")


@router.get("/tasks/{thread_id}/timeline")
async def get_task_timeline(
    thread_id: str,
    start_index: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """Get paginated timeline events."""
    try:
        history_store = HistoryStore()
        await history_store.ensure_initialized()
        
        timeline = await history_store.get_task_timeline(thread_id)
        
        total = len(timeline)
        events = timeline[start_index:start_index + limit]
        
        return {
            "thread_id": thread_id,
            "events": events,
            "total": total,
            "start_index": start_index,
            "limit": limit,
            "has_more": start_index + limit < total,
        }
    except Exception as e:
        logger.exception("history.get_timeline_failed", thread_id=thread_id, error=str(e))
        raise HTTPException(500, f"Failed to get timeline: {str(e)}")


@router.get("/tasks/{thread_id}/runs")
async def list_task_runs(
    thread_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Return the durable run envelopes behind one task, newest first."""
    membership = await session.scalar(
        select(OrganizationMember).where(OrganizationMember.user_id == user.id).order_by(OrganizationMember.created_at.asc())
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="No workspace is associated with this account.")
    task = await session.scalar(
        select(TaskThread).where(
            TaskThread.external_thread_id == thread_id,
            TaskThread.user_id == user.id,
            TaskThread.organization_id == membership.organization_id,
        )
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    runs = list((await session.scalars(
        select(AgentRun)
        .options(selectinload(AgentRun.attempts), selectinload(AgentRun.evidence), selectinload(AgentRun.artifacts))
        .where(AgentRun.task_id == task.id)
        .order_by(AgentRun.run_number.desc())
    )).all())
    return {
        "thread_id": thread_id,
        "runs": [{
            "id": str(run.id),
            "run_number": run.run_number,
            "status": run.status,
            "intent": run.intent,
            "plan": run.plan,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "error": run.error,
            "attempt_count": len(run.attempts),
            "evidence_count": len(run.evidence),
            "artifact_count": len(run.artifacts),
        } for run in runs],
    }


@router.get("/tasks/{thread_id}/export")
async def export_task(thread_id: str, format: str = "json"):
    """Export task history in various formats."""
    try:
        history_store = HistoryStore()
        await history_store.ensure_initialized()
        
        task = await history_store.get_full_task(thread_id)
        if not task:
            raise HTTPException(404, f"Task {thread_id} not found")
        
        if format == "json":
            return task
        elif format == "markdown":
            return await _export_markdown(task)
        else:
            raise HTTPException(400, f"Unsupported format: {format}")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("history.export_failed", thread_id=thread_id, error=str(e))
        raise HTTPException(500, f"Failed to export task: {str(e)}")


async def _export_markdown(task: dict) -> dict:
    """Convert task history to markdown."""
    lines = []
    lines.append(f"# Task: {task.get('task', 'Untitled')}")
    lines.append("")
    lines.append(f"- **Thread ID:** {task.get('thread_id')}")
    lines.append(f"- **Status:** {task.get('status')}")
    lines.append(f"- **Created:** {task.get('created_at')}")
    lines.append(f"- **Completed:** {task.get('completed_at', 'N/A')}")
    lines.append(f"- **Duration:** {task.get('duration_seconds', 0):.1f}s")
    lines.append(f"- **Cost:** ${task.get('total_cost_usd', 0):.6f}")
    lines.append(f"- **Tool Calls:** {task.get('tool_calls_count', 0)}")
    lines.append("")
    
    if task.get("summary"):
        lines.append("## Summary")
        lines.append("")
        lines.append(task["summary"])
        lines.append("")
    
    lines.append("## Timeline")
    lines.append("")
    
    timeline = task.get("timeline", [])
    for i, event in enumerate(timeline):
        event_type = event.get("type", "unknown")
        timestamp = event.get("timestamp", "")
        data = event.get("data", {})
        
        lines.append(f"### {i + 1}. {event_type.replace('_', ' ').title()}")
        lines.append(f"- **Time:** {timestamp}")
        
        if "step" in data:
            lines.append(f"- **Step:** {data['step']}")
        if "tool" in data:
            lines.append(f"- **Tool:** {data['tool']}")
        if "output" in data:
            lines.append(f"- **Output:** {data['output'][:200]}...")
        if "status" in data:
            lines.append(f"- **Status:** {data['status']}")
        if "error" in data:
            lines.append(f"- **Error:** {data['error']}")
        
        lines.append("")
    
    return {
        "format": "markdown",
        "content": "\n".join(lines),
        "filename": f"task_{task.get('thread_id')}.md"
    }


@router.delete("/tasks/{thread_id}")
async def delete_task(thread_id: str):
    """Delete a task history."""
    try:
        history_store = HistoryStore()
        await history_store.ensure_initialized()
        
        deleted = await history_store.delete_task(thread_id)
        if not deleted:
            raise HTTPException(404, f"Task {thread_id} not found")
        
        return {"status": "deleted", "thread_id": thread_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("history.delete_failed", thread_id=thread_id, error=str(e))
        raise HTTPException(500, f"Failed to delete task: {str(e)}")


@router.get("/stats")
async def get_stats(user_id: str = Query("dashboard")):
    """Get task statistics."""
    try:
        history_store = HistoryStore()
        await history_store.ensure_initialized()
        
        stats = await history_store.get_task_stats(user_id)
        return stats
    except Exception as e:
        logger.exception("history.stats_failed", error=str(e))
        raise HTTPException(500, f"Failed to get stats: {str(e)}")


@router.get("/metrics/autonomy")
async def get_autonomy_metrics(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Get autonomy metrics for the user's organization."""
    tasks = list((await session.scalars(
        select(TaskThread).where(TaskThread.organization_id == user.organization_id)
    )).all())
    
    total = len(tasks)
    completed = len([t for t in tasks if t.status == "completed"])
    failed = len([t for t in tasks if t.status == "failed"])
    paused = len([t for t in tasks if t.status == "paused"])
    running = len([t for t in tasks if t.status == "running"])
    
    autonomous_rate = completed / total if total > 0 else 0
    
    return {
        "period": {
            "start": (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),
            "end": datetime.now(timezone.utc).isoformat(),
        },
        "total_tasks": total,
        "completed": completed,
        "failed": failed,
        "paused": paused,
        "running": running,
        "autonomous_completion_rate": round(autonomous_rate, 2),
        "success_rate": round(completed / (completed + failed) if (completed + failed) > 0 else 0, 2),
        "health_status": "good" if autonomous_rate > 0.8 else "needs_attention" if autonomous_rate > 0.5 else "critical",
    }


@router.get("/learnings")
async def get_learnings(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Get learnings from past task executions."""
    failed_tasks = list((await session.scalars(
        select(TaskThread).where(
            TaskThread.organization_id == user.organization_id,
            TaskThread.status == "failed",
        ).order_by(TaskThread.updated_at.desc()).limit(20)
    )).all())
    
    failures = []
    for task in failed_tasks:
        events = list((await session.scalars(
            select(TaskEvent).where(
                TaskEvent.task_id == task.id,
                TaskEvent.event_type == "task_error",
            ).order_by(TaskEvent.created_at.desc())
        )).all())
        
        for event in events:
            payload = event.payload or {}
            failures.append({
                "task_id": task.external_thread_id,
                "task_title": task.title,
                "error": payload.get("message") or payload.get("error") or "Unknown error",
                "timestamp": event.created_at.isoformat(),
            })
    
    error_counts = {}
    for failure in failures:
        error = failure["error"][:100]
        error_counts[error] = error_counts.get(error, 0) + 1
    
    patterns = []
    for error, count in sorted(error_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
        patterns.append({
            "error_pattern": error,
            "occurrences": count,
            "recommendation": "Consider adding retry logic" if "timeout" in error.lower() else "Consider human oversight" if "auth" in error.lower() else "Consider refining the plan"
        })
    
    return {
        "total_failures": len(failures),
        "common_errors": patterns,
        "learnings": [
            f"Task '{f['task_title']}' failed with: {f['error'][:100]}"
            for f in failures[:5]
        ],
    }