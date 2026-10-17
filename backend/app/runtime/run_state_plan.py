"""Phase 2 helper: attach / read hierarchical plans on RunState."""
from __future__ import annotations

from typing import Any

from app.runtime.hierarchical_plan import HierarchicalPlan


def set_plan_on_state(state: Any, plan: HierarchicalPlan | dict[str, Any] | None) -> None:
    """Store a hierarchical plan on a RunState-like object."""
    if plan is None:
        data = None
    elif isinstance(plan, HierarchicalPlan):
        data = plan.to_dict()
    else:
        data = plan
    try:
        state.hierarchical_plan = data
    except Exception:
        # Fallback for plain namespaces / dicts
        if isinstance(state, dict):
            state["hierarchical_plan"] = data
        else:
            setattr(state, "hierarchical_plan", data)


def get_plan_from_state(state: Any) -> HierarchicalPlan | None:
    raw = getattr(state, "hierarchical_plan", None)
    if raw is None and isinstance(state, dict):
        raw = state.get("hierarchical_plan")
    if raw is None and isinstance(getattr(state, "role_config", None), dict):
        raw = state.role_config.get("hierarchical_plan")
    if not raw:
        return None
    if isinstance(raw, HierarchicalPlan):
        return raw
    if isinstance(raw, dict):
        return HierarchicalPlan.from_dict(raw)
    return None


__all__ = ["set_plan_on_state", "get_plan_from_state"]
