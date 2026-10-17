"""Phase 6: model strategy — detailed unit tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.runtime.model_strategy import (
    StrategyLedger,
    TaskSignals,
    decide_and_record,
    infer_signals,
    select_strategy,
)


# ---------------------------------------------------------------------------
# Signal inference
# ---------------------------------------------------------------------------

def test_infer_research_from_goal():
    s = infer_signals(goal="Research competitor pricing and cite sources")
    assert s.needs_research is True


def test_infer_code_from_tools():
    s = infer_signals(goal="fix the bug", tools_used=["python_exec", "file_edit"])
    assert s.needs_code is True


def test_infer_browser_from_goal():
    s = infer_signals(goal="Open the website and click the login button")
    assert s.needs_browser is True


def test_infer_verification_role_hint():
    s = infer_signals(goal="check result", role_hint="verifier")
    assert s.is_verification is True


def test_infer_spreadsheet_and_document():
    assert infer_signals(goal="build an excel spreadsheet with formulas").needs_spreadsheet
    assert infer_signals(goal="draft a pdf report document").needs_document


# ---------------------------------------------------------------------------
# Lane selection
# ---------------------------------------------------------------------------

def test_default_lane_is_planner():
    d = select_strategy(TaskSignals(goal="Do something vague"))
    assert d.lane == "planner"
    assert d.router_role == "planning"


def test_verification_selects_verifier_lane():
    d = select_strategy(TaskSignals(is_verification=True))
    assert d.lane == "verifier"
    assert d.router_role == "verification"
    assert d.prefer_tools is False


def test_classification_selects_fast():
    d = select_strategy(TaskSignals(is_classification=True))
    assert d.lane == "fast"


def test_browser_beats_code_when_both():
    # Vision/browser takes priority over code in the decision tree
    d = select_strategy(TaskSignals(needs_browser=True, needs_code=True))
    assert d.lane == "browser"


def test_code_selects_coder():
    d = select_strategy(TaskSignals(needs_code=True))
    assert d.lane == "coder"
    assert d.prefer_tools is True


def test_research_depth_standard_selects_researcher():
    d = select_strategy(TaskSignals(research_depth="standard"))
    assert d.lane == "researcher"


def test_explicit_lane_wins():
    d = select_strategy(TaskSignals(explicit_lane="spreadsheet", needs_code=True))
    assert d.lane == "spreadsheet"
    assert any("explicit_lane" in r for r in d.reasons)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

def test_economy_profile_prefers_deepseek_and_lower_tokens():
    d = select_strategy(TaskSignals(goal="plan a trip"), profile="economy")
    assert d.profile == "economy"
    assert d.max_tokens <= 2048
    assert d.fallback_chain[0] == "deepseek"
    assert d.temperature <= 0.15


def test_quality_profile_higher_tokens():
    d = select_strategy(TaskSignals(goal="plan a trip"), profile="quality")
    assert d.max_tokens >= 8192
    assert d.temperature >= 0.25


def test_failover_aggressive_shortens_timeout():
    balanced = select_strategy(TaskSignals(goal="x"), profile="balanced")
    aggressive = select_strategy(TaskSignals(goal="x"), profile="failover_aggressive")
    assert aggressive.timeout_s <= balanced.timeout_s
    assert aggressive.timeout_s >= 10


def test_deep_research_boosts_max_tokens():
    d = select_strategy(TaskSignals(needs_research=True, research_depth="deep"), profile="balanced")
    assert d.lane == "researcher"
    assert d.max_tokens >= 8192
    assert any("depth:deep" in r for r in d.reasons)


# ---------------------------------------------------------------------------
# Timeouts & env overrides
# ---------------------------------------------------------------------------

def test_timeout_defaults_positive():
    for lane_signal in (
        TaskSignals(is_classification=True),
        TaskSignals(is_verification=True),
        TaskSignals(needs_code=True),
        TaskSignals(needs_browser=True),
    ):
        d = select_strategy(lane_signal)
        assert d.timeout_s >= 5


def test_timeout_env_override(monkeypatch):
    monkeypatch.setenv("MODEL_TIMEOUT_PLANNER", "42")
    d = select_strategy(TaskSignals(goal="generic planning task"))
    assert d.lane == "planner"
    assert d.timeout_s == 42


def test_profile_from_env(monkeypatch):
    monkeypatch.setenv("MODEL_STRATEGY_PROFILE", "economy")
    d = select_strategy(TaskSignals(goal="hello"))
    assert d.profile == "economy"


# ---------------------------------------------------------------------------
# Fallback chains
# ---------------------------------------------------------------------------

def test_verifier_fallback_chain_starts_with_kimi_or_deepseek():
    d = select_strategy(TaskSignals(is_verification=True))
    assert d.fallback_chain[0] in ("kimi", "deepseek")


def test_browser_fallback_prefers_nvidia():
    d = select_strategy(TaskSignals(needs_browser=True))
    assert d.fallback_chain[0] == "nvidia"


def test_fallback_chain_unique_and_nonempty():
    d = select_strategy(TaskSignals(goal="x"))
    assert len(d.fallback_chain) >= 2
    assert len(d.fallback_chain) == len(set(d.fallback_chain))


# ---------------------------------------------------------------------------
# Ledger / diagnostics
# ---------------------------------------------------------------------------

def test_ledger_records_decisions():
    ledger = StrategyLedger()
    d1 = decide_and_record(TaskSignals(needs_code=True), ledger=ledger)
    d2 = decide_and_record(TaskSignals(is_verification=True), ledger=ledger)
    assert ledger.last() == d2
    summary = ledger.summary()
    assert summary["count"] == 2
    assert summary["by_lane"]["coder"] == 1
    assert summary["by_lane"]["verifier"] == 1
    assert summary["last"]["lane"] == "verifier"


def test_decision_to_dict_serializable():
    d = select_strategy(TaskSignals(needs_research=True))
    payload = d.to_dict()
    assert isinstance(payload["fallback_chain"], list)
    assert isinstance(payload["reasons"], list)
    assert payload["lane"] == "researcher"


# ---------------------------------------------------------------------------
# Priority matrix (parametrized)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "signals,expected_lane",
    [
        (TaskSignals(is_verification=True, needs_code=True), "verifier"),
        (TaskSignals(is_classification=True, needs_research=True), "fast"),
        (TaskSignals(needs_vision=True), "browser"),
        (TaskSignals(needs_spreadsheet=True, needs_document=True), "spreadsheet"),
        (TaskSignals(needs_document=True), "document"),
        (TaskSignals(needs_research=True), "researcher"),
        (TaskSignals(needs_code=True), "coder"),
        (TaskSignals(), "planner"),
    ],
)
def test_priority_matrix(signals, expected_lane):
    d = select_strategy(signals)
    assert d.lane == expected_lane
