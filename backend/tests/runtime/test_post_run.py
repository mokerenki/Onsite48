"""Workstream C: post-run finalize."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.runtime.post_run import finalize_run


def test_finalize_run_memory_and_artifacts():
    with tempfile.TemporaryDirectory() as td:
        Path(td, "out.txt").write_text("deliverable", encoding="utf-8")
        out = finalize_run(
            run_id="run-x",
            goal="Research Acme and always prefer primary sources",
            finish_status="completed",
            finish_summary="- Acme pricing starts at 49 dollars per month on the official site.",
            journal_events=[{
                "type": "tool_call_result",
                "payload": {"name": "web_search", "result": {"ok": True}},
            }],
            workspace_root=td,
        )
        assert out.get("memory") and out["memory"]["count"] >= 1
        assert out.get("artifacts") and out["artifacts"]["count"] >= 1


def test_finalize_run_no_workspace_still_memory():
    out = finalize_run(
        run_id="run-y",
        goal="always prefer concise answers",
        finish_summary="- Delivered a concise summary of the findings from primary sources online.",
        workspace_root=None,
    )
    assert out.get("memory") is not None
    assert out.get("artifacts") is None
