# backend/app/core/checkpoint_manager.py - New file for checkpoint management

import json
import structlog
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List

from app.core.context import onsite48

logger = structlog.get_logger("onsite48.checkpoint_manager")


class CheckpointManager:
    """Manage task checkpoints for pause/resume functionality."""
    
    def __init__(self):
        self.redis = onsite48.redis_client
        self.checkpointer = onsite48.checkpointer
    
    async def get_current_state(self, thread_id: str) -> Optional[dict]:
        """Get the current state of a task."""
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await self.checkpointer.aget_tuple(config)
        if snapshot:
            return snapshot.checkpoint.get("channel_values", {})
        return None
    
    async def pause_task(self, thread_id: str, reason: str = "User requested pause") -> dict:
        """Pause a running task and return its state."""
        state = await self.get_current_state(thread_id)
        if not state:
            raise ValueError(f"No task found for thread {thread_id}")
        
        # Check if already paused
        if state.get("status") == "paused":
            return {"status": "already_paused", "thread_id": thread_id}
        
        # Check if task is in a pausable state
        if state.get("status") in ["completed", "failed"]:
            raise ValueError(f"Task {thread_id} is already {state.get('status')}")
        
        # Update state
        state["status"] = "paused"
        state["paused_at"] = datetime.now(timezone.utc).isoformat()
        state["pause_reason"] = reason
        
        # Store paused state
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await self.checkpointer.aget_tuple(config)
        await self.checkpointer.aput(
            config,
            snapshot.checkpoint,
            {"status": "paused", "paused_at": state["paused_at"]},
            {}
        )
        
        # Store in Redis for quick lookup
        pause_key = f"onsite48:paused:{thread_id}"
        await self.redis.setex(
            pause_key,
            86400,  # 24 hours
            json.dumps({
                "thread_id": thread_id,
                "paused_at": state["paused_at"],
                "reason": reason,
                "state_preview": {
                    "task": state.get("task"),
                    "current_step": state.get("current_step", 0),
                    "plan_length": len(state.get("plan", [])),
                    "results_count": len(state.get("results", [])),
                }
            })
        )
        
        logger.info(f"Task {thread_id} paused", extra={"reason": reason})
        return {
            "status": "paused",
            "thread_id": thread_id,
            "paused_at": state["paused_at"],
            "state": state
        }
    
    async def resume_task(
        self, 
        thread_id: str, 
        modifications: Optional[dict] = None
    ) -> dict:
        """Resume a paused task with optional modifications."""
        # Get paused state
        pause_key = f"onsite48:paused:{thread_id}"
        paused_data = await self.redis.get(pause_key)
        if not paused_data:
            raise ValueError(f"No paused task found for {thread_id}")
        
        # Get full state
        state = await self.get_current_state(thread_id)
        if not state:
            raise ValueError(f"No state found for {thread_id}")
        
        # Apply modifications
        if modifications:
            # Update plan if provided
            if "plan" in modifications:
                state["plan"] = modifications["plan"]
                state["current_step"] = 0
                state["results"] = []
                state["final_answer"] = None
            
            # Skip specific steps
            if "skip_steps" in modifications:
                skipped = set(modifications["skip_steps"])
                state["skipped_steps"] = {i: True for i in skipped}
                # Update current step to skip over them
                new_results = []
                for i, result in enumerate(state.get("results", [])):
                    if i not in skipped:
                        new_results.append(result)
                state["results"] = new_results
            
            # Update any other state fields
            for key, value in modifications.items():
                if key not in ["plan", "skip_steps"]:
                    state[key] = value
        
        # Update status
        state["status"] = "running"
        state["resumed_at"] = datetime.now(timezone.utc).isoformat()
        
        # Remove from paused list
        await self.redis.delete(pause_key)
        
        logger.info(f"Task {thread_id} resumed")
        return {
            "status": "resumed",
            "thread_id": thread_id,
            "resumed_at": state["resumed_at"],
            "state": state
        }
    
    async def get_paused_tasks(self, limit: int = 50) -> List[dict]:
        """Get all paused tasks."""
        pattern = "onsite48:paused:*"
        keys = await self.redis.keys(pattern)
        
        paused_tasks = []
        for key in keys[:limit]:
            key_str = key.decode() if isinstance(key, bytes) else key
            data = await self.redis.get(key_str)
            if data:
                task_data = json.loads(data)
                # Get full state for more details
                thread_id = task_data.get("thread_id")
                full_state = await self.get_current_state(thread_id)
                if full_state:
                    task_data["state_preview"] = {
                        "task": full_state.get("task"),
                        "current_step": full_state.get("current_step", 0),
                        "plan": full_state.get("plan", []),
                        "results": full_state.get("results", []),
                    }
                paused_tasks.append(task_data)
        
        return paused_tasks
    
    async def modify_step(self, thread_id: str, step_index: int, new_step: dict) -> dict:
        """Modify a specific step in the plan."""
        state = await self.get_current_state(thread_id)
        if not state:
            raise ValueError(f"No state found for {thread_id}")
        
        plan = state.get("plan", [])
        if step_index >= len(plan):
            raise ValueError(f"Step {step_index} does not exist (plan has {len(plan)} steps)")
        
        # Replace the step
        plan[step_index] = new_step
        
        # Update state
        state["plan"] = plan
        
        # If the modified step is before or at current step, reset to that step
        current_step = state.get("current_step", 0)
        if step_index < current_step:
            state["current_step"] = step_index
            state["results"] = state.get("results", [])[:step_index]
            state["final_answer"] = None
        
        # Store updated state
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await self.checkpointer.aget_tuple(config)
        await self.checkpointer.aput(
            config,
            snapshot.checkpoint,
            state,
            {}
        )
        
        return {
            "status": "modified",
            "thread_id": thread_id,
            "step_index": step_index,
            "new_step": new_step,
            "plan": plan
        }