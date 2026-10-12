"""In-process task execution control signals for explicit human takeover and cancellation.

The durable TaskThread status remains the source of truth for UI restoration and
resumption. This small registry solves a separate live-runtime problem: a task
stream can already be executing when the user presses Take over. Each takeover
increments a generation, allowing the existing stream to notice that it is
obsolete and exit after its current bounded graph event even if the user has
already returned control by the time that event arrives.

Cancellation uses the same thread_id-keyed approach: the runtime loop registers
its cancel_event, and the cancel API endpoint sets it so the in-flight loop
exits within a bounded window instead of continuing uninterrupted.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class TaskExecutionControlState:
    generation: int
    user_control_active: bool


class TaskExecutionControl:
    def __init__(self) -> None:
        self._generation: dict[str, int] = {}
        self._user_control_active: set[str] = set()
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    async def snapshot_generation(self, thread_id: str) -> int:
        async with self._lock:
            return self._generation.get(thread_id, 0)

    async def start_user_takeover(self, thread_id: str) -> int:
        async with self._lock:
            generation = self._generation.get(thread_id, 0) + 1
            self._generation[thread_id] = generation
            self._user_control_active.add(thread_id)
            return generation

    async def return_to_agent_ready(self, thread_id: str) -> TaskExecutionControlState:
        async with self._lock:
            self._user_control_active.discard(thread_id)
            return TaskExecutionControlState(
                generation=self._generation.get(thread_id, 0),
                user_control_active=False,
            )

    async def stream_was_superseded(self, thread_id: str, generation: int) -> bool:
        async with self._lock:
            return self._generation.get(thread_id, 0) != generation

    async def register_cancel_event(self, thread_id: str, event: asyncio.Event) -> None:
        async with self._lock:
            self._cancel_events[thread_id] = event

    async def signal_cancel(self, thread_id: str) -> bool:
        async with self._lock:
            event = self._cancel_events.get(thread_id)
            if event is not None:
                event.set()
                return True
            return False

    async def lookup_cancel_event(self, thread_id: str) -> asyncio.Event | None:
        async with self._lock:
            return self._cancel_events.get(thread_id)

    async def clear(self, thread_id: str) -> None:
        async with self._lock:
            self._user_control_active.discard(thread_id)
            self._generation.pop(thread_id, None)
            self._cancel_events.pop(thread_id, None)

    async def unregister_cancel_event(self, thread_id: str) -> None:
        async with self._lock:
            self._cancel_events.pop(thread_id, None)


# The task graph and authenticated API handlers run inside one backend process.
# A process restart destroys Agent Computer contexts anyway, so this intentionally
# does not claim cross-process continuity.
task_execution_control = TaskExecutionControl()