"""Phase 0: budget_profiles depth caps, aliases, and key survival."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.core.budget_profiles import (
    DEEP_MAX_STEPS,
    DEEP_MAX_WALL_SECONDS,
    QUICK_MAX_STEPS,
    QUICK_MAX_WALL_SECONDS,
    budgets_for_depth,
    normalize_depth,
)
from app.core.task_budgets import initial_task_budgets


def test_normalize_aliases_and_invalid():
    assert normalize_depth("fast") == "quick"
    assert normalize_depth("thorough") == "deep"
    assert normalize_depth("QUICK") == "quick"
    assert normalize_depth("nope") == "standard"
    assert normalize_depth(None) == "standard"


def test_quick_profile_caps():
    b = budgets_for_depth("quick")
    assert b["research_depth"] == "quick"
    assert b["max_steps"] == QUICK_MAX_STEPS
    assert b["max_wall_seconds"] == QUICK_MAX_WALL_SECONDS
    assert b["max_subagents"] == 0


def test_deep_profile_caps():
    b = budgets_for_depth("deep")
    assert b["research_depth"] == "deep"
    assert b["max_steps"] == DEEP_MAX_STEPS
    assert b["max_wall_seconds"] == DEEP_MAX_WALL_SECONDS


def test_unknown_keys_from_base_survive():
    base = initial_task_budgets()
    b = budgets_for_depth("standard")
    for key in base:
        assert key in b
    assert b["research_depth"] == "standard"
