"""Research-depth budget profiles for agent runs.

Merges depth-specific overrides on top of initial_task_budgets() so unknown
keys from the base profile always survive.
"""
from __future__ import annotations

import os
from typing import Any

from app.core.task_budgets import initial_task_budgets


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


QUICK_MAX_STEPS = _env_int("QUICK_MAX_STEPS", 8)
QUICK_MAX_WALL_SECONDS = _env_float("QUICK_MAX_WALL_SECONDS", 180.0)
DEEP_MAX_STEPS = _env_int("DEEP_MAX_STEPS", 120)
DEEP_MAX_WALL_SECONDS = _env_float("DEEP_MAX_WALL_SECONDS", 3600.0)
DEEP_MAX_SUBAGENTS = _env_int("DEEP_MAX_SUBAGENTS", 4)

_ALIASES = {
    "fast": "quick",
    "thorough": "deep",
    "quick": "quick",
    "standard": "standard",
    "deep": "deep",
}


def normalize_depth(depth: Any) -> str:
    """Normalize a depth label; invalid values become standard."""
    if depth is None:
        return "standard"
    key = str(depth).strip().lower()
    return _ALIASES.get(key, "standard")


def budgets_for_depth(depth: Any) -> dict[str, Any]:
    """Return initial_task_budgets() merged with depth profile overrides."""
    base = dict(initial_task_budgets())
    d = normalize_depth(depth)
    base["research_depth"] = d
    if d == "quick":
        base["max_steps"] = QUICK_MAX_STEPS
        base["max_wall_seconds"] = QUICK_MAX_WALL_SECONDS
        base["max_subagents"] = 0
    elif d == "deep":
        base["max_steps"] = DEEP_MAX_STEPS
        base["max_wall_seconds"] = DEEP_MAX_WALL_SECONDS
        base["max_subagents"] = DEEP_MAX_SUBAGENTS
    # standard: no overrides beyond research_depth key
    return base


__all__ = [
    "normalize_depth",
    "budgets_for_depth",
    "QUICK_MAX_STEPS",
    "QUICK_MAX_WALL_SECONDS",
    "DEEP_MAX_STEPS",
    "DEEP_MAX_WALL_SECONDS",
    "DEEP_MAX_SUBAGENTS",
]
