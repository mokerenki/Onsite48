# backend/app/core/history_store.py

import json
import structlog
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List
from enum import Enum

from app.core.context import onsite48

logger = structlog.get_logger("onsite48.history_store")

# TTL: 30 days for full history
HISTORY_TTL_SECONDS = 86400 * 30
# Max events per task to prevent memory issues
MAX_EVENTS_PER_TASK = 500


class EventType(str, Enum):
    """Types of events in the execution timeline."""
    TASK_START = "task_start"
    PLAN_GENERATED = "plan_generated"
    STEP_START = "step_start"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    STEP_COMPLETE = "step_complete"
    VERIFICATION = "verification"
    REPLAN = "replan"
    TASK_COMPLETE = "task_complete"
    TASK_FAILED = "task_failed"
    TASK_PAUSED = "task_paused"
    TASK_RESUMED = "task_resumed"
    USER_CONFIRMATION = "user_confirmation"
    ERROR = "error"


class HistoryStore:
    """
    Stores complete execution history for tasks.
    Enables replay and debugging.
    """
    
    def __init__(self):
        self.redis = onsite48.redis_client
        self._initialized = False
    
    async def ensure_initialized(self):
        if not self._initialized:
            onsite48.ensure_initialized()
            self.redis = onsite48.redis_client
            self._initialized = True
    
    def _history_key(self, thread_id: str) -> str:
        return f"onsite48:history:full:{thread_id}"
    
    def _timeline_key(self, thread_id: str) -> str:
        return f"onsite48:history:timeline:{thread_id}"
    
    def _events_key(self, thread_id: str) -> str:
        return f"onsite48:history:events:{thread_id}"
    
    async def create_task_record(
        self,
        thread_id: str,
        task: str,
        user_id: str,
        tenant_id: str
    ) -> None:
        """Initialize a new task history record."""
        await self.ensure_initialized()
        
        record = {
            "thread_id": thread_id,
            "task": task,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "duration_seconds": None,
            "total_cost_usd": 0.0,
            "tool_calls_count": 0,
            "events_count": 0,
            "summary": None,
            "tags": [],
        }
        
        await self.redis.setex(
            self._history_key(thread_id),
            HISTORY_TTL_SECONDS,
            json.dumps(record)
        )
        
        # Initialize timeline
        await self.redis.setex(
            self._timeline_key(thread_id),
            HISTORY_TTL_SECONDS,
            json.dumps([])
        )
        
        logger.info("history.task_record_created", thread_id=thread_id, task=task[:50])
    
    async def add_event(
        self,
        thread_id: str,
        event_type: EventType,
        data: Dict[str, Any],
        step_index: Optional[int] = None
    ) -> None:
        """Add an event to the task timeline."""
        await self.ensure_initialized()
        
        event = {
            "type": event_type.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
            "step_index": step_index,
        }
        
        # Add to timeline
        timeline_key = self._timeline_key(thread_id)
        timeline_data = await self.redis.get(timeline_key)
        timeline = json.loads(timeline_data) if timeline_data else []
        
        # Limit events to prevent memory issues
        if len(timeline) >= MAX_EVENTS_PER_TASK:
            logger.warning("history.max_events_reached", thread_id=thread_id, count=len(timeline))
            return
        
        timeline.append(event)
        await self.redis.setex(timeline_key, HISTORY_TTL_SECONDS, json.dumps(timeline))
        
        # Update event count in record
        await self._increment_event_count(thread_id)
        
        logger.debug("history.event_added", thread_id=thread_id, type=event_type.value, step=step_index)
    
    async def _increment_event_count(self, thread_id: str) -> None:
        """Increment the event count in the task record."""
        key = self._history_key(thread_id)
        data = await self.redis.get(key)
        if data:
            record = json.loads(data)
            record["events_count"] = record.get("events_count", 0) + 1
            await self.redis.setex(key, HISTORY_TTL_SECONDS, json.dumps(record))
    
    async def update_task_status(
        self,
        thread_id: str,
        status: str,
        final_answer: Optional[str] = None,
        cost_metrics: Optional[Dict] = None,
        error: Optional[str] = None
    ) -> None:
        """Update the task record with final status."""
        await self.ensure_initialized()
        
        key = self._history_key(thread_id)
        data = await self.redis.get(key)
        if not data:
            return
        
        record = json.loads(data)
        record["status"] = status
        
        if final_answer:
            record["summary"] = final_answer[:500]  # Truncate for summary
        
        if cost_metrics:
            record["total_cost_usd"] = cost_metrics.get("total_cost_usd", 0)
            record["tool_calls_count"] = cost_metrics.get("tool_calls", 0)
        
        if status in ["completed", "failed"]:
            record["completed_at"] = datetime.now(timezone.utc).isoformat()
            if record.get("created_at"):
                created = datetime.fromisoformat(record["created_at"])
                completed = datetime.fromisoformat(record["completed_at"])
                record["duration_seconds"] = (completed - created).total_seconds()
        
        if error:
            record["error"] = error
        
        await self.redis.setex(key, HISTORY_TTL_SECONDS, json.dumps(record))
        logger.info("history.task_updated", thread_id=thread_id, status=status)
    
    async def get_task_history(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """Get the full history record for a task."""
        await self.ensure_initialized()
        
        key = self._history_key(thread_id)
        data = await self.redis.get(key)
        if data:
            return json.loads(data)
        return None
    
    async def get_task_timeline(self, thread_id: str) -> List[Dict[str, Any]]:
        """Get the event timeline for a task."""
        await self.ensure_initialized()
        
        timeline_key = self._timeline_key(thread_id)
        data = await self.redis.get(timeline_key)
        if data:
            return json.loads(data)
        return []
    
    async def get_full_task(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """Get both record and timeline for a task."""
        record = await self.get_task_history(thread_id)
        if not record:
            return None
        
        timeline = await self.get_task_timeline(thread_id)
        record["timeline"] = timeline
        return record
    
    async def list_user_tasks(
        self,
        user_id: str,
        limit: int = 50,
        status: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List tasks for a user with filtering."""
        await self.ensure_initialized()
        
        pattern = "onsite48:history:full:*"
        keys = await self.redis.keys(pattern)
        
        tasks = []
        for key in keys:
            key_str = key.decode() if isinstance(key, bytes) else key
            data = await self.redis.get(key_str)
            if data:
                record = json.loads(data)
                
                # Filter by user
                if record.get("user_id") != user_id:
                    continue
                
                # Filter by status
                if status and record.get("status") != status:
                    continue
                
                # Filter by date range
                if from_date:
                    created = record.get("created_at")
                    if created and created < from_date:
                        continue
                if to_date:
                    created = record.get("created_at")
                    if created and created > to_date:
                        continue
                
                # Return summary (not full timeline for list view)
                tasks.append({
                    "thread_id": record.get("thread_id"),
                    "task": record.get("task", "")[:100],
                    "status": record.get("status"),
                    "created_at": record.get("created_at"),
                    "completed_at": record.get("completed_at"),
                    "duration_seconds": record.get("duration_seconds"),
                    "total_cost_usd": record.get("total_cost_usd", 0),
                    "events_count": record.get("events_count", 0),
                    "summary": record.get("summary"),
                })
        
        tasks.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return tasks[:limit]
    
    async def delete_task(self, thread_id: str) -> bool:
        """Delete a task history (cleanup)."""
        await self.ensure_initialized()
        
        keys = [
            self._history_key(thread_id),
            self._timeline_key(thread_id),
        ]
        
        deleted = 0
        for key in keys:
            result = await self.redis.delete(key)
            deleted += result
        
        return deleted > 0
    
    async def get_task_stats(self, user_id: str) -> Dict[str, Any]:
        """Get statistics about tasks for a user."""
        tasks = await self.list_user_tasks(user_id, limit=1000)
        
        total = len(tasks)
        completed = len([t for t in tasks if t.get("status") == "completed"])
        failed = len([t for t in tasks if t.get("status") == "failed"])
        paused = len([t for t in tasks if t.get("status") == "paused"])
        
        total_cost = sum(t.get("total_cost_usd", 0) for t in tasks)
        total_events = sum(t.get("events_count", 0) for t in tasks)
        
        avg_duration = 0
        durations = [t.get("duration_seconds", 0) for t in tasks if t.get("duration_seconds")]
        if durations:
            avg_duration = sum(durations) / len(durations)
        
        return {
            "total_tasks": total,
            "completed_tasks": completed,
            "failed_tasks": failed,
            "paused_tasks": paused,
            "total_cost_usd": total_cost,
            "total_events": total_events,
            "avg_duration_seconds": avg_duration,
        }