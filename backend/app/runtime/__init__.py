"""Onsite48 agent runtime: one tool-calling loop (Phase 1–3).

Activated when AGENT_RUNTIME=loop (default as of Phase 3 cutover).
"""
from __future__ import annotations

__all__ = ["run"]


async def run(run_id: str, goal: str, role_config: dict | None = None, **kwargs):
    from app.runtime.loop import run as _run
    return await _run(run_id, goal, role_config or {}, **kwargs)

# Phase 5
from app.runtime.roles_v2 import RoleConfig, get_role  # noqa: F401
from app.runtime.spawn import SpawnManager, wide_map  # noqa: F401
from app.runtime.locks import ResourceLockManager, get_lock_manager  # noqa: F401
# Phase 6
from app.runtime.metrics import RunMetrics, StepMetrics, aggregate_run_metrics  # noqa: F401
from app.runtime.lane_defaults import LANE_DEFAULTS, model_for_lane  # noqa: F401
