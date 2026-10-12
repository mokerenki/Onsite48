"""Chat-native automation intent endpoint.

The route is intentionally separate from task execution: natural language may
preview a durable automation, but persistence requires an explicit confirmation.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.files import workspace_membership
from app.api.proactive import ChatAutomationIntentRequest, parse_chat_automation
from app.core.security import get_current_user, get_db_session
from app.models import ScheduledAgentTask, User

router = APIRouter(prefix="/api/proactive", tags=["chat automation"])


@router.post("/scheduled-tasks/from-chat")
async def automation_from_chat(payload: ChatAutomationIntentRequest, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    intent = parse_chat_automation(payload.message, payload.timezone)
    preview = {
        "title": intent["title"], "prompt": intent["prompt"],
        "schedule_type": intent["schedule_type"], "timezone": intent["timezone"],
        "run_at": intent["run_at"], "schedule_policy": intent["policy"],
    }
    if not payload.confirm:
        return {"created": False, "requires_confirmation": True, "intent": preview}
    membership = await workspace_membership(session, user)
    task = ScheduledAgentTask(
        organization_id=membership.organization_id,
        user_id=user.id,
        title=intent["title"],
        prompt=intent["prompt"],
        schedule_type=intent["schedule_type"],
        timezone=intent["timezone"],
        schedule_policy=intent["policy"],
        run_at=intent["run_at"],
        next_run_at=intent["run_at"],
        autonomy_policy={"mode": "approval_required", "source": "chat"},
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return {"created": True, "requires_confirmation": False, "schedule": {"id": str(task.id), **preview}}