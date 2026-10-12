"""Authenticated, thread-scoped memory API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.files import workspace_membership
from app.core.context import onsite48
from app.core.security import get_current_user, get_db_session
from app.models import TaskThread, User

logger = structlog.get_logger("onsite48.api.memory")
router = APIRouter(prefix="/api/memory", tags=["memory"])


async def owned_thread(session: AsyncSession, user: User, organization_id: Any, thread_id: str) -> TaskThread:
    task = await session.scalar(select(TaskThread).where(
        TaskThread.external_thread_id == thread_id,
        TaskThread.user_id == user.id,
        TaskThread.organization_id == organization_id,
    ))
    if task is None:
        raise HTTPException(status_code=404, detail="Task thread not found.")
    return task


async def memory_scope(request: dict[str, Any], user: User, session: AsyncSession) -> dict[str, str]:
    membership = await workspace_membership(session, user)
    thread_id = str(request.get("thread_id") or "").strip()
    if not thread_id:
        raise HTTPException(status_code=400, detail="thread_id is required for tenant-safe memory access.")
    await owned_thread(session, user, membership.organization_id, thread_id)
    return {"organization_id": str(membership.organization_id), "user_id": str(user.id), "thread_id": thread_id}


@router.post("/search")
async def search_memory(request: dict[str, Any], user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    onsite48.ensure_initialized()
    if onsite48.cognee_memory is None:
        raise HTTPException(status_code=503, detail="Memory service not initialized.")
    mode = str(request.get("mode") or "")
    query = str(request.get("query") or "")
    if not mode or not query:
        raise HTTPException(status_code=400, detail="Both mode and query are required.")
    scope = await memory_scope(request, user, session)
    kwargs: dict[str, Any] = {
        "query": query,
        "mode": mode,
        "top_k": max(1, min(50, int(request.get("top_k") or 5))),
        "entity_label": request.get("entity_label"),
        "entity_id": request.get("entity_id"),
        **scope,
    }
    if mode == "temporal":
        raw_time = request.get("query_time")
        if not kwargs["entity_label"] or not kwargs["entity_id"] or not raw_time:
            raise HTTPException(status_code=400, detail="entity_label, entity_id, and query_time are required for temporal mode.")
        kwargs["property_name"] = query
        kwargs["query_time"] = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
    try:
        return await onsite48.cognee_memory.search(**kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("memory_api.search_failed", mode=mode)
        raise HTTPException(status_code=500, detail="Memory search failed.") from exc


@router.post("/ingest")
async def ingest_event(request: dict[str, Any], user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    onsite48.ensure_initialized()
    if onsite48.cognee_memory is None:
        raise HTTPException(status_code=503, detail="Memory service not initialized.")
    content = str(request.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required.")
    scope = await memory_scope(request, user, session)
    event = {"content": content, "source": str(request.get("source") or "api"), "event_id": request.get("event_id"), **scope}
    try:
        await onsite48.cognee_memory.ingest(event)
        return {"status": "success", "event_id": event.get("event_id")}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("memory_api.ingestion_failed")
        raise HTTPException(status_code=500, detail="Memory ingestion failed.") from exc


@router.get("/health")
async def memory_health(user: User = Depends(get_current_user)) -> dict[str, Any]:
    onsite48.ensure_initialized()
    if onsite48.cognee_memory is None or not onsite48.cognee_memory._initialized:
        return JSONResponse(status_code=503, content={"status": "unavailable", "message": "Memory service is not initialized."})
    return {"status": "healthy", "message": "Memory service is tenant-scoped."}


@router.get("/stats")
async def memory_stats(user: User = Depends(get_current_user)) -> dict[str, Any]:
    onsite48.ensure_initialized()
    if onsite48.cognee_memory is None:
        raise HTTPException(status_code=503, detail="Memory service not initialized.")
    stats = {"initialized": onsite48.cognee_memory._initialized, "qdrant_collection": onsite48.cognee_memory.qdrant_collection_name}
    if onsite48.cognee_memory.qdrant_client and onsite48.cognee_memory._initialized:
        try:
            stats["qdrant_points_count"] = onsite48.cognee_memory.qdrant_client.get_collection(onsite48.cognee_memory.qdrant_collection_name).points_count
        except Exception:
            stats["qdrant_points_count"] = None
    return stats