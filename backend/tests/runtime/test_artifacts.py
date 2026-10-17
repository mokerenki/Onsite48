"""Phase 9: artifact packaging tests."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.runtime.artifacts import collect_workspace_artifacts, manifest_from_paths


def test_collect_workspace_artifacts():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "out").mkdir()
        f = root / "out" / "report.txt"
        f.write_text("hello world", encoding="utf-8")
        m = collect_workspace_artifacts(root, run_id="run1", goal="write report", finish_status="completed")
        assert m.count == 1
        assert m.items[0].name == "report.txt"
        assert m.items[0].sha256
        assert m.total_bytes > 0
        d = m.to_dict()
        assert d["run_id"] == "run1"


def test_manifest_from_paths():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "a.bin"
        p.write_bytes(b"abc")
        m = manifest_from_paths([str(p)], run_id="r", goal="g", base=td)
        assert m.count == 1
        assert m.items[0].size_bytes == 3
