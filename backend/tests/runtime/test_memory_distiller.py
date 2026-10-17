"""Phase 8: memory distiller tests."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.runtime.memory_distiller import InMemoryMemoryStore, distill_run


def test_distill_extracts_facts_and_preferences():
    out = distill_run(
        run_id="r1",
        goal="Research Acme and always prefer primary sources",
        finish_summary="- Acme pricing starts at 49 dollars per month on the official pricing page.\n- Globex has a free tier.",
        journal_events=[{
            "type": "tool_call_result",
            "payload": {"name": "web_search", "result": {"ok": True}},
        }, {
            "type": "tool_call_result",
            "payload": {"name": "file_write", "result": {"ok": True}},
        }],
        verifier={"missing_evidence": ["second source for Globex"], "unmet_requirements": []},
    )
    assert out.count >= 2
    kinds = {r.kind for r in out.records}
    assert "fact" in kinds or "preference" in kinds
    assert any(r.kind == "open_question" for r in out.records)
    assert any(r.kind == "procedure" for r in out.records)


def test_memory_store_search():
    out = distill_run(
        run_id="r2",
        goal="g",
        finish_summary="- NeonCorp launched a new API gateway product in Europe this quarter.",
    )
    store = InMemoryMemoryStore()
    store.upsert_many(out.records)
    hits = store.search("NeonCorp API gateway")
    assert hits
    assert hits[0].source_run_id == "r2"
