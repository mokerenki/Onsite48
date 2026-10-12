"""Intent extractor — retired with Phase 3 cutover.

The runtime loop does not use regex/intent parsing. This module remains as a
no-op stub so any residual import does not crash the process.
"""
from __future__ import annotations
from typing import Any

def extract_intent(goal: str, **kwargs: Any) -> dict[str, Any]:
    return {"intent": "generic", "goal": goal, "source": "noop_phase3"}

def resolve_intent(goal: str, **kwargs: Any) -> dict[str, Any]:
    return extract_intent(goal, **kwargs)
