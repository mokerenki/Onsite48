from __future__ import annotations

from typing import Any, Dict, List, Optional


class StepRegistry:
    def __init__(self) -> None:
        self._steps: Dict[str, Dict[str, Any]] = {}
        self._completed: Dict[str, Dict[str, Any]] = {}
        self._failed: Dict[str, str] = {}

    def register_step(
        self,
        step_id: str,
        description: str = "",
        tool_name: Optional[str] = None,
        dependencies: Optional[List[str]] = None,
        output_schema: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._steps[step_id] = {
            "description": description,
            "tool_name": tool_name,
            "dependencies": dependencies or [],
            "output_schema": output_schema,
        }

    def step_completed(self, step_id: str, tool_data: Dict[str, Any]) -> None:
        self._completed[step_id] = {
            "tool_data": tool_data,
            "completed_at": __import__("datetime").datetime.now().isoformat(),
        }

    def step_failed(self, step_id: str, error: str) -> None:
        self._failed[step_id] = str(error)

    def get_step(self, step_id: str) -> Optional[Dict[str, Any]]:
        return self._steps.get(step_id)

    def get_completed(self, step_id: str) -> Optional[Dict[str, Any]]:
        return self._completed.get(step_id)

    def get_failed(self, step_id: str) -> Optional[str]:
        return self._failed.get(step_id)

    def is_completed(self, step_id: str) -> bool:
        return step_id in self._completed

    def is_failed(self, step_id: str) -> bool:
        return step_id in self._failed

    def pending_steps(self) -> List[str]:
        return [
            step_id
            for step_id in self._steps
            if step_id not in self._completed and step_id not in self._failed
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "steps": self._steps,
            "completed": self._completed,
            "failed": self._failed,
        }


def get_step_registry() -> StepRegistry:
    return StepRegistry()