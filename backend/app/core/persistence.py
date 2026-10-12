# backend/app/core/persistence.py

"""
Task persistence utilities - shared between tasks.py and websocket_handler.py
Breaks the circular import between tasks.py and websocket_handler.py.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List  # <-- FIXED: Added List

from app.core.context import onsite48

TASK_PROGRESS_PREFIX = "onsite48:task:progress"
TASK_STATUS_PREFIX = "onsite48:task:status"
TASK_TTL_SECONDS = 86400  # 24 hours


def _get_context():
    """Get the Onsite48 context. Raises clear error if not initialized."""
    onsite48.ensure_initialized()
    return onsite48


async def persist_task_progress(thread_id: str, state: Dict[str, Any]) -> None:
    """Persist incremental task progress to Redis."""
    ctx = _get_context()
    key = f"{TASK_PROGRESS_PREFIX}:{thread_id}"
    payload = {
        "thread_id": thread_id,
        "task": state.get("task", ""),
        "status": state.get("status", "pending"),
        "current_step": state.get("current_step", 0),
        "plan": json.dumps(state.get("plan", [])),
        "results": json.dumps(state.get("results", [])),
        "final_answer": state.get("final_answer") or "",
        "cost_metrics": json.dumps(state.get("cost_metrics", {})),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await ctx.redis_client.hset(key, mapping=payload)
    await ctx.redis_client.expire(key, TASK_TTL_SECONDS)


async def get_task_progress(thread_id: str) -> Optional[Dict[str, Any]]:
    """Get task progress from Redis."""
    ctx = _get_context()
    key = f"{TASK_PROGRESS_PREFIX}:{thread_id}"
    data = await ctx.redis_client.hgetall(key)
    if not data:
        return None
    result = {
        k.decode() if isinstance(k, bytes) else k: 
        v.decode() if isinstance(v, bytes) else v 
        for k, v in data.items()
    }
    for field in ("plan", "results", "cost_metrics"):
        val = result.get(field)
        if val:
            try:
                result[field] = json.loads(val)
            except Exception:
                pass
    return result


async def get_task_metadata(task_id: str) -> Optional[Dict[str, Any]]:
    """Fetch task metadata from Redis."""
    ctx = _get_context()
    key = f"{TASK_STATUS_PREFIX}:{task_id}"
    data = await ctx.redis_client.hgetall(key)
    if not data:
        return None
    return {
        k.decode() if isinstance(k, bytes) else k: 
        v.decode() if isinstance(v, bytes) else v 
        for k, v in data.items()
    }


async def set_task_metadata(task_id: str, metadata: Dict[str, Any]) -> None:
    """Store task metadata in Redis."""
    ctx = _get_context()
    key = f"{TASK_STATUS_PREFIX}:{task_id}"
    encoded = {
        k: str(v) if not isinstance(v, str) else v 
        for k, v in metadata.items()
    }
    await ctx.redis_client.hset(key, mapping=encoded)
    await ctx.redis_client.expire(key, TASK_TTL_SECONDS)


# ─── Phase 0: Task History ─────────────────────────────────────


async def store_checkpoint_snapshot(thread_id: str, checkpoint: dict) -> None:
    """
    Store full checkpoint state for history replay.
    Called from the executor after each node execution.
    """
    ctx = _get_context()
    key = f"onsite48:checkpoint:history:{thread_id}"
    
    # Get existing history or create new
    existing = await ctx.redis_client.get(key)
    history = json.loads(existing) if existing else {"thread_id": thread_id, "snapshots": []}
    
    # Add new snapshot with timestamp
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "state": {
            "plan": checkpoint.get("plan", []),
            "current_step": checkpoint.get("current_step", 0),
            "results": checkpoint.get("results", []),
            "status": checkpoint.get("status", "pending"),
            "final_answer": checkpoint.get("final_answer"),
            "cost_metrics": checkpoint.get("cost_metrics", {}),
            "verification": checkpoint.get("verification"),
        }
    }
    
    history["snapshots"].append(snapshot)
    
    # Keep only last 100 snapshots to prevent memory issues
    if len(history["snapshots"]) > 100:
        history["snapshots"] = history["snapshots"][-100:]
    
    await ctx.redis_client.setex(key, 86400 * 30, json.dumps(history))  # 30 days TTL


async def get_task_history(thread_id: str) -> Optional[dict]:
    """Retrieve full task history for a thread."""
    ctx = _get_context()
    key = f"onsite48:checkpoint:history:{thread_id}"
    data = await ctx.redis_client.get(key)
    if data:
        return json.loads(data)
    return None


async def list_user_tasks(user_id: str, limit: int = 50) -> List[dict]:
    """List all tasks for a user with metadata."""
    ctx = _get_context()
    pattern = f"onsite48:task:status:*"
    keys = await ctx.redis_client.keys(pattern)
    
    tasks = []
    for key in keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        task_id = key_str.split(":")[-1]
        metadata = await get_task_metadata(task_id)
        if metadata and metadata.get("user_id") == user_id:
            tasks.append({
                "task_id": task_id,
                "thread_id": metadata.get("thread_id"),
                "task": metadata.get("task"),
                "status": metadata.get("status"),
                "created_at": metadata.get("created_at"),
                "completed_at": metadata.get("completed_at"),
                "cost_metrics": json.loads(metadata.get("cost_metrics", "{}")),
                "final_answer": metadata.get("final_answer"),
            })
    
    tasks.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return tasks[:limit]


class TaskMemory:
    """Store and retrieve learnings from past task executions."""

    def __init__(self):
        self.namespace = "onsite48:task_memory"

    def _key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    def _fingerprint(self, goal: str, tools: List[str]) -> str:
        content = f"{goal}|{sorted(tools)}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _get_redis(self):
        ctx = _get_context()
        return ctx.redis_client

    async def record_execution(
        self,
        goal: str,
        tools_used: List[str],
        plan: List[Dict[str, Any]],
        outcome: str,
        duration_seconds: float,
        success: bool,
        failure_reason: Optional[str] = None,
        learnings: Optional[str] = None,
    ) -> None:
        """Record a task execution for future learning."""
        redis = self._get_redis()
        fingerprint = self._fingerprint(goal, tools_used)

        record = {
            "fingerprint": fingerprint,
            "goal": goal,
            "tools_used": tools_used,
            "plan": plan,
            "outcome": outcome,
            "duration_seconds": duration_seconds,
            "success": success,
            "failure_reason": failure_reason,
            "learnings": learnings,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        record_key = self._key(f"record:{fingerprint}:{datetime.now().timestamp()}")
        await redis.setex(record_key, 30 * 86400, json.dumps(record))

        pattern_key = self._key(f"pattern:{fingerprint}")
        pattern_data = await redis.get(pattern_key)
        pattern = json.loads(pattern_data) if pattern_data else {}

        pattern["executions"] = pattern.get("executions", 0) + 1
        pattern["successes"] = pattern.get("successes", 0) + (1 if success else 0)
        pattern["last_execution"] = record["timestamp"]

        if success:
            pattern["last_successful_plan"] = plan
            pattern["last_successful_outcome"] = outcome

        if failure_reason:
            failures = pattern.get("common_failures", {})
            failures[failure_reason] = failures.get(failure_reason, 0) + 1
            pattern["common_failures"] = failures

        await redis.setex(pattern_key, 90 * 86400, json.dumps(pattern))

    async def get_plan_suggestion(self, goal: str, tools: List[str]) -> Optional[List[Dict[str, Any]]]:
        """Get a suggested plan from past successful executions."""
        redis = self._get_redis()
        fingerprint = self._fingerprint(goal, tools)
        pattern_key = self._key(f"pattern:{fingerprint}")
        pattern_data = await redis.get(pattern_key)

        if not pattern_data:
            return None

        pattern = json.loads(pattern_data)
        if pattern.get("executions", 0) == 0:
            return None

        # Find the most recent successful execution
        records = []
        async for key in redis.scan_iter(f"{self.namespace}:record:{fingerprint}:*"):
            data = await redis.get(key)
            if data:
                record = json.loads(data)
                if record.get("success") and record.get("plan"):
                    records.append(record)

        if not records:
            return None

        records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return records[0].get("plan")


# Global instance
task_memory = TaskMemory()