"""Retired task API compatibility shim.

Interactive execution is authoritative in ``app.api.realtime`` and
``app.api.websocket_handler``. Scheduled/heartbeat dispatch lives in
``app.services.proactive_worker``. This module intentionally contains no
parallel websocket executor, Redis task-progress writer, or task-creation
endpoint.
"""

from fastapi import APIRouter

from app.services.proactive_worker import create_proactive_task_to_queue

router = APIRouter(prefix="/api/tasks", tags=["retired task compatibility"])

__all__ = ["router", "create_proactive_task_to_queue"]