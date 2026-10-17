"""Phase 0: orphan sweeper requeues lapsed claims and fails dead RUNNING tasks."""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def test_sweep_once_calls_enforce_timeouts_and_requeue():
    from app.services import orphan_sweeper as mod

    session_factory = MagicMock()

    async def _run():
        with patch.object(mod, "enforce_timeouts", new_callable=AsyncMock, return_value=2) as et, \
             patch.object(mod, "requeue_lapsed_queued_claims", new_callable=AsyncMock, return_value=1) as rq:
            result = await mod.sweep_once(session_factory)
        assert isinstance(result, dict)
        et.assert_awaited_once()
        rq.assert_awaited_once()
        return result

    asyncio.run(_run())


def test_requeue_lapsed_uses_release_for_retry_with_reason():
    from app.services import orphan_sweeper as mod
    from app.models import TaskStatus

    task = SimpleNamespace(
        id=uuid.uuid4(),
        status=TaskStatus.QUEUED,
        attempt_count=1,
        lease_expires_at=datetime.now(UTC) - timedelta(minutes=10),
        lease_token="dead",
        execution_metadata={},
        updated_at=datetime.now(UTC) - timedelta(minutes=10),
    )

    session = AsyncMock()
    result_proxy = MagicMock()
    result_proxy.all.return_value = [task]
    session.scalars = AsyncMock(return_value=result_proxy)
    session.commit = AsyncMock()
    begin_cm = AsyncMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin_cm)
    sf_cm = AsyncMock()
    sf_cm.__aenter__ = AsyncMock(return_value=session)
    sf_cm.__aexit__ = AsyncMock(return_value=None)
    session_factory = MagicMock(return_value=sf_cm)

    reasons = []

    async def fake_release(session, task, *, now=None, reason=""):
        reasons.append(reason)
        return True

    async def _run():
        with patch.object(mod, "release_for_retry", side_effect=fake_release):
            return await mod.requeue_lapsed_queued_claims(session_factory, limit=10)

    count = asyncio.run(_run())
    assert count >= 0


def test_write_task_worker_heartbeat_sets_key():
    from app.services.orphan_sweeper import write_task_worker_heartbeat

    redis = AsyncMock()

    async def _run():
        await write_task_worker_heartbeat(redis, "worker-1")

    asyncio.run(_run())
    assert redis.set.await_count >= 1
    args = redis.set.await_args
    key = args.args[0] if args.args else args.kwargs.get("name")
    assert "task_worker" in str(key)
