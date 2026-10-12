"""Explicit memory retention and deletion worker entry points."""

from __future__ import annotations

from typing import Any, Iterable


async def purge_expired(memory_backend: Any) -> dict[str, Any]:
    purge = getattr(memory_backend, "purge_expired", None)
    if purge is None:
        return {"status": "unavailable", "reason": "memory backend has no retention contract"}
    result = purge()
    if hasattr(result, "__await__"):
        result = await result
    return result if isinstance(result, dict) else {"status": "ok", "result": result}


async def delete_scopes(memory_backend: Any, scopes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    delete = getattr(memory_backend, "delete_scope", None)
    if delete is None:
        return [{"status": "unavailable", "reason": "memory backend has no scoped deletion contract"}]
    results = []
    for scope in scopes:
        result = delete(**scope)
        if hasattr(result, "__await__"):
            result = await result
        results.append(result if isinstance(result, dict) else {"status": "ok", "result": result})
    return results


__all__ = ["delete_scopes", "purge_expired"]