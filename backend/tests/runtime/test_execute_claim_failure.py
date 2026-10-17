"""Phase 0: execute_claim records FAILED + task_error when loop.run raises."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@pytest.mark.asyncio
async def test_fail_claimed_task_writes_failed_status():
    from app.services.task_worker_entrypoint import _fail_claimed_task
    from app.models import TaskStatus

    claim = SimpleNamespace(
        id=uuid.uuid4(),
        external_thread_id="thread-1",
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        prompt="do something",
        attempt=1,
        lease_token="tok",
        research_depth=None,
    )

    task = SimpleNamespace(
        id=claim.id,
        status=TaskStatus.RUNNING,
        completed_at=None,
        execution_metadata={},
    )

    session = AsyncMock()
    session.get = AsyncMock(return_value=task)
    session.commit = AsyncMock()
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=None)
    session_factory = MagicMock(return_value=session_cm)

    record = AsyncMock()
    with patch("app.api.realtime.record_event", record):
        await _fail_claimed_task(session_factory, claim, RuntimeError("boom"))

    assert task.status == TaskStatus.FAILED
    assert task.completed_at is not None


@pytest.mark.asyncio
async def test_execute_claim_propagates_after_fail_helper():
    from app.services import task_worker_entrypoint as twe

    claim = SimpleNamespace(
        id=uuid.uuid4(),
        external_thread_id="thread-1",
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        prompt="do something",
        attempt=1,
        lease_token="tok",
        research_depth=None,
    )

    fail_helper = AsyncMock()
    with patch.object(twe, "is_cancelled", new_callable=AsyncMock, return_value=False), \
         patch.object(twe, "mark_started", new_callable=AsyncMock, return_value=True), \
         patch.object(twe, "_fail_claimed_task", fail_helper), \
         patch.object(twe, "get_session_factory", return_value=MagicMock()), \
         patch.object(twe, "heartbeat_claim", new_callable=AsyncMock, return_value=True), \
         patch("app.runtime.loop.run", AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError, match="boom"):
            await twe.execute_claim(claim)

    fail_helper.assert_awaited()
