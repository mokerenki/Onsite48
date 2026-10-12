"""Provider-normalized model cost accounting for a single task state."""

from __future__ import annotations

from typing import Any


def response_cost_usd(response: Any) -> float | None:
    cost = getattr(response, "cost_usd", None)
    if isinstance(cost, (int, float)) and cost >= 0:
        return float(cost)
    return None


def _ensure_buckets(metrics: dict[str, Any]) -> dict[str, Any]:
    metrics.setdefault("total_cost_usd", 0.0)
    metrics.setdefault("known_model_calls", 0)
    metrics.setdefault("unknown_cost_model_calls", 0)
    metrics.setdefault("by_provider", {})
    metrics.setdefault("by_role", {})
    # Unpriced exposure. A provider whose price is unset reports cost_usd=None,
    # which previously meant the call vanished from the total and the task
    # reported $0.00 as though it were a measured fact. It was a lower bound
    # presented as an exact number.
    metrics.setdefault("unpriced_input_tokens", 0)
    metrics.setdefault("unpriced_output_tokens", 0)
    metrics.setdefault("total_cost_usd_is_lower_bound", False)
    return metrics


def unpriced_token_fraction(metrics: dict[str, Any] | None) -> float:
    """Share of tokens whose spend is unmeasured, 0.0 to 1.0.

    This is the number an operator actually needs: a task that reports $0.00
    with 200k unpriced tokens is not free, it is unmeasured.
    """
    if not metrics:
        return 0.0
    unpriced = int(metrics.get("unpriced_input_tokens", 0) or 0) + int(
        metrics.get("unpriced_output_tokens", 0) or 0
    )
    if unpriced <= 0:
        return 0.0
    total = 0
    for record in (metrics.get("by_provider") or {}).values():
        if not isinstance(record, dict):
            continue
        total += int(record.get("input_tokens", 0) or 0)
        total += int(record.get("output_tokens", 0) or 0)
    if total <= 0:
        return 1.0
    return min(1.0, unpriced / total)


def record_model_cost(state: dict[str, Any], response: Any, *, role: str | None = None) -> None:
    """Append one normalized cost record; never substitute another provider's price.

    An unpriced call still contributes its tokens, and marks the total as a
    lower bound, so an operator can see the unmeasured exposure instead of
    trusting a confident $0.00.
    """
    metrics = _ensure_buckets(dict(state.get("cost_metrics", {}) or {}))

    provider = str(getattr(response, "provider", "unknown") or "unknown")
    model = str(getattr(response, "model", "unknown") or "unknown")
    usage = getattr(response, "usage", None)
    cost = response_cost_usd(response)
    key = f"{provider}:{model}"
    provider_record = dict(metrics["by_provider"].get(key, {}) or {})
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    provider_record["input_tokens"] = int(provider_record.get("input_tokens", 0) or 0) + input_tokens
    provider_record["output_tokens"] = int(provider_record.get("output_tokens", 0) or 0) + output_tokens

    if cost is None:
        metrics["unknown_cost_model_calls"] = int(metrics["unknown_cost_model_calls"] or 0) + 1
        metrics["unpriced_input_tokens"] = int(metrics["unpriced_input_tokens"] or 0) + input_tokens
        metrics["unpriced_output_tokens"] = int(metrics["unpriced_output_tokens"] or 0) + output_tokens
        metrics["total_cost_usd_is_lower_bound"] = True
        provider_record["unknown_cost_calls"] = int(provider_record.get("unknown_cost_calls", 0) or 0) + 1
    else:
        metrics["total_cost_usd"] = float(metrics["total_cost_usd"] or 0.0) + cost
        metrics["known_model_calls"] = int(metrics["known_model_calls"] or 0) + 1
        provider_record["cost_usd"] = float(provider_record.get("cost_usd", 0.0) or 0.0) + cost

    metrics["by_provider"][key] = provider_record
    if role:
        role_record = dict(metrics["by_role"].get(role, {}) or {})
        role_record["calls"] = int(role_record.get("calls", 0) or 0) + 1
        role_record["cost_usd"] = float(role_record.get("cost_usd", 0.0) or 0.0) + (cost or 0.0)
        role_record["unknown_cost_calls"] = int(role_record.get("unknown_cost_calls", 0) or 0) + (1 if cost is None else 0)
        metrics["by_role"][role] = role_record
    state["cost_metrics"] = metrics


def record_code_execution_cost(state: dict[str, Any], provider: str, model: str, usage: Any, cost_usd: float) -> None:
    """Record the model call that generated CodeAct code as code execution."""
    metrics = _ensure_buckets(dict(state.get("cost_metrics", {}) or {}))
    metrics["total_cost_usd"] = float(metrics["total_cost_usd"] or 0.0) + float(cost_usd or 0.0)
    # This is a priced call, so it counts as known. It previously added to the
    # total without incrementing known_model_calls, leaving known + unknown
    # permanently short of the real number of calls.
    metrics["known_model_calls"] = int(metrics.get("known_model_calls", 0) or 0) + 1
    role = dict(metrics["by_role"].get("code_execution", {}) or {})
    role["calls"] = int(role.get("calls", 0) or 0) + 1
    role["cost_usd"] = float(role.get("cost_usd", 0.0) or 0.0) + float(cost_usd or 0.0)
    # Accumulated, not overwritten: the previous assignment kept only the last
    # provider, losing the model mix of earlier calls in a multi-provider task.
    providers = list(role.get("providers", []) or [])
    provider_name = str(provider or "unknown")
    if provider_name not in providers:
        providers.append(provider_name)
    role["providers"] = providers
    role["provider"] = provider_name
    role["model"] = str(model or "unknown")
    role["usage"] = usage if isinstance(usage, dict) else getattr(usage, "__dict__", {})
    metrics["by_role"]["code_execution"] = role
    state["cost_metrics"] = metrics