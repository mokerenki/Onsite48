"""Contracts for resumable user clarification checkpoints."""

from __future__ import annotations

from typing import Any


def clarification_question_for_step(step: dict[str, Any]) -> str:
    """Return the user question encoded by a tool-less clarification step."""
    if not isinstance(step, dict) or step.get("tool_name"):
        return ""
    args = step.get("tool_args") if isinstance(step.get("tool_args"), dict) else {}
    return str(step.get("clarification_question") or args.get("question") or "").strip()


__all__ = ["clarification_question_for_step"]