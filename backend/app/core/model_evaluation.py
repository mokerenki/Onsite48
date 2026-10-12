"""Small, provider-neutral evaluation primitives for agent model routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class EvaluationCase:
    name: str
    prompt: str
    expected_tools: tuple[str, ...] = ()
    required_phrases: tuple[str, ...] = ()


@dataclass
class EvaluationResult:
    case: str
    passed: bool
    score: float
    observed_tools: list[str] = field(default_factory=list)
    missing_phrases: list[str] = field(default_factory=list)
    error: str | None = None


def evaluate_response(case: EvaluationCase, response: Any, *, observed_tools: list[str] | None = None) -> EvaluationResult:
    """Score deterministic contract signals without judging prose style."""
    content = str(getattr(response, "content", response) or "").lower()
    tools = list(observed_tools or [])
    missing_tools = [tool for tool in case.expected_tools if tool not in tools]
    missing_phrases = [phrase for phrase in case.required_phrases if phrase.lower() not in content]
    total = len(case.expected_tools) + len(case.required_phrases)
    failed = len(missing_tools) + len(missing_phrases)
    score = 1.0 if total == 0 else max(0.0, (total - failed) / total)
    return EvaluationResult(case=case.name, passed=failed == 0, score=score, observed_tools=tools, missing_phrases=missing_tools + missing_phrases)


async def run_evaluation_suite(cases: list[EvaluationCase], runner: Callable[[str], Any]) -> list[EvaluationResult]:
    """Run a supplied planner/model runner while keeping evaluation side-effect free."""
    results: list[EvaluationResult] = []
    for case in cases:
        try:
            response = await runner(case.prompt)
            results.append(evaluate_response(case, response))
        except Exception as exc:
            results.append(EvaluationResult(case=case.name, passed=False, score=0.0, error=str(exc)))
    return results