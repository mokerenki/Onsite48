"""Post-run memory distillation + artifact packaging (Workstream C)."""
from __future__ import annotations

import os
from typing import Any

from app.runtime.artifacts import collect_workspace_artifacts
from app.runtime.memory_distiller import InMemoryMemoryStore, distill_run

# Process-local sink for demos/tests; production can swap via set_memory_sink.
_default_sink = InMemoryMemoryStore()
_memory_sink: Any = _default_sink


def post_run_distill_enabled() -> bool:
    return os.environ.get("ENABLE_POST_RUN_DISTILL", "1").lower() not in ("0", "false", "no", "off")


def post_run_artifacts_enabled() -> bool:
    return os.environ.get("ENABLE_POST_RUN_ARTIFACTS", "1").lower() not in ("0", "false", "no", "off")


def set_memory_sink(sink: Any) -> None:
    global _memory_sink
    _memory_sink = sink


def get_memory_sink() -> Any:
    return _memory_sink


def finalize_run(
    *,
    run_id: str,
    goal: str,
    finish_status: str = "",
    finish_summary: str = "",
    journal_events: list[dict[str, Any]] | None = None,
    key_results: list[dict[str, Any]] | None = None,
    verifier: dict[str, Any] | None = None,
    workspace_root: str | None = None,
) -> dict[str, Any]:
    """Best-effort post-run packaging. Never raises to callers."""
    out: dict[str, Any] = {"memory": None, "artifacts": None}
    try:
        if post_run_distill_enabled():
            distilled = distill_run(
                run_id=run_id,
                goal=goal or "",
                finish_summary=finish_summary or "",
                journal_events=journal_events,
                key_results=key_results,
                verifier=verifier,
            )
            sink = _memory_sink
            if sink is not None and hasattr(sink, "upsert_many"):
                try:
                    sink.upsert_many(distilled.records)
                except Exception as exc:
                    out["memory_sink_error"] = str(exc)[:200]
            out["memory"] = distilled.to_dict()
    except Exception as exc:
        out["memory_error"] = str(exc)[:200]

    try:
        if post_run_artifacts_enabled() and workspace_root:
            manifest = collect_workspace_artifacts(
                workspace_root,
                run_id=run_id,
                goal=goal or "",
                finish_status=finish_status or "",
                finish_summary=finish_summary or "",
            )
            out["artifacts"] = manifest.to_dict()
    except Exception as exc:
        out["artifacts_error"] = str(exc)[:200]

    return out


__all__ = [
    "finalize_run",
    "set_memory_sink",
    "get_memory_sink",
    "post_run_distill_enabled",
    "post_run_artifacts_enabled",
]
