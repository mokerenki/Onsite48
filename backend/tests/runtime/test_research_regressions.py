"""Regression tests for the long-research-run fixes."""
import pytest

from app.runtime.research_control import (
    finish_gate_exhausted,
    is_retrieval_tool,
    plan_research,
    research_finish_blocked,
    saturation_nudge_message,
    verifier_evidence,
    weighted_retrieval_count,
    weighted_retrieval_increment,
)

GOAL = "research the difference between tensa and manus"


def _ledger(n_search: int, n_other: int):
    searches = [{"tool": "web_search", "result": {"ok": True, "results": ["x"]}} for _ in range(n_search)]
    others = [{"tool": "file_write", "result": {"ok": True}} for _ in range(n_other)]
    return searches + others


class TestVerifierEvidence:
    def test_retrievals_survive_long_ledger(self):
        evidence = verifier_evidence(_ledger(10, 40), max_entries=20)
        kept = [e for e in evidence if is_retrieval_tool(str(e.get("tool") or ""))]
        assert len(kept) == 10  # blind [-20:] would keep 0

    def test_small_ledger_passes_through(self):
        ledger = _ledger(2, 3)
        assert verifier_evidence(ledger, max_entries=20) is ledger


class TestRetrievalCountOverride:
    def test_finish_gate_uses_override(self):
        plan = plan_research(GOAL)
        assert plan.is_research
        assert research_finish_blocked(GOAL, [], retrieval_count=0)
        assert research_finish_blocked(GOAL, [], retrieval_count=plan.min_retrievals_before_finish) is None

    def test_saturation_uses_override(self):
        deep_goal = "write a detailed, comprehensive research report on X"
        assert saturation_nudge_message(deep_goal, [], already_nudged=False, retrieval_count=12)
        assert saturation_nudge_message(deep_goal, [], already_nudged=False, retrieval_count=1) is None


class TestWideResearchWeighting:
    def test_wide_research_counts_branches(self):
        result = {"ok": True, "agent_count": 6, "branches": []}
        assert weighted_retrieval_increment("wide_research", result) == 6
        assert weighted_retrieval_increment("web_search", {"ok": True}) == 1
        assert weighted_retrieval_increment("wide_research", {"ok": False}) == 0

    def test_weighted_count_over_ledger(self):
        ledger = [{"tool": "wide_research", "result": {"ok": True, "agent_count": 6}}]
        assert weighted_retrieval_count(ledger) == 6


class TestFinishGateCap:
    def test_exhaustion_triggers_failure(self):
        assert finish_gate_exhausted(3, 0, 1)
        assert not finish_gate_exhausted(2, 0, 1)
        assert not finish_gate_exhausted(3, 2, 1)