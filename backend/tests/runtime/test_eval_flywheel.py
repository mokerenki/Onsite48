"""Phase 10: eval flywheel tests."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evals.flywheel import EVAL_TASKS, run_flywheel, summarize


def test_eval_tasks_nonempty_and_ids_unique():
    assert len(EVAL_TASKS) >= 10
    ids = [t.id for t in EVAL_TASKS]
    assert len(ids) == len(set(ids))


def test_run_flywheel_all_pass():
    rows = run_flywheel()
    summary = summarize(rows)
    assert summary["total"] == len(EVAL_TASKS)
    assert summary["infra_failures"] == 0
    assert summary["pass_rate"] == 1.0
    assert all(r.pass_ for r in rows)


def test_run_flywheel_writes_jsonl():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "out.jsonl"
        rows = run_flywheel(out_path=path)
        assert path.exists()
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == len(rows)
        obj = json.loads(lines[0])
        assert "pass" in obj and "failure_tag" in obj
