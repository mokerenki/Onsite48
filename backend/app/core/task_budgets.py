"""Independent safety budgets for one agent task execution."""

from __future__ import annotations

MAX_PLANNER_ATTEMPTS = 6
MAX_EXECUTED_STEPS_ABSOLUTE = 90
MIN_EXECUTED_STEPS = 16
RECOVERY_STEP_ALLOWANCE = 16
MAX_REPLANS = 6


def execution_step_limit(plan: object) -> int:
    """Allow a valid plan to finish while retaining a finite runaway guard."""
    plan_length = len(plan) if isinstance(plan, list) else 0
    return min(
        MAX_EXECUTED_STEPS_ABSOLUTE,
        max(MIN_EXECUTED_STEPS, plan_length + RECOVERY_STEP_ALLOWANCE),
    )


def initial_task_budgets() -> dict[str, int]:
    return {
        "planner_attempts": 0,
        "executed_steps": 0,
        "execution_step_limit": 0,
        "tool_retry_count": 0,
    }