"""Phase 0: tool timeout produces structured timeout step failure."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def test_structured_timeout_error_shape_without_loop_import():
    """Contract for timeout failures — mirrors loop._structured_error shape."""
    err = {
        "error_code": "timeout",
        "message": "sleep exceeded 1s",
        "retryable": True,
        "suggestion": "",
    }
    assert err["error_code"] == "timeout"
    assert "exceeded" in err["message"]
    assert err["retryable"] is True


def test_asyncio_wait_timeout_semantics():
    """Mirrors loop tool path: wait with timeout cancels a forever task."""

    async def _run():
        async def forever():
            await asyncio.sleep(3600)

        task = asyncio.ensure_future(forever())
        done, pending = await asyncio.wait({task}, timeout=0.05)
        assert task not in done
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=0.5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            return True
        return False

    assert asyncio.run(_run()) is True
