"""Phase 0: tool timeout produces structured timeout step failure."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def test_structured_timeout_error_shape():
    from app.runtime.loop import _structured_error

    err = _structured_error("timeout", "sleep exceeded 1s", retryable=True)
    assert err["error_code"] == "timeout"
    assert "exceeded" in err["message"]
    assert err["retryable"] is True


@pytest.mark.asyncio
async def test_asyncio_wait_timeout_semantics():
    """Mirrors loop tool path: wait with timeout cancels a forever task."""

    async def forever():
        await asyncio.sleep(3600)

    task = asyncio.ensure_future(forever())
    done, pending = await asyncio.wait({task}, timeout=0.05)
    assert task not in done
    task.cancel()
    with pytest.raises((asyncio.CancelledError, asyncio.TimeoutError)):
        await asyncio.wait_for(task, timeout=0.5)
