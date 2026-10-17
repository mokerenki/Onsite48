"""Tests for Fix 4 — durable SQL memory store round-trip."""
from __future__ import annotations

import os
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.runtime.memory_distiller import SQLMemoryStore, MemoryRecord


@pytest.fixture
async def session_factory():
    """In-memory SQLite session factory (no external DB required)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_store_roundtrip(session_factory):
    store = SQLMemoryStore(session_factory)
    rec = MemoryRecord(
        id="m1",
        kind="fact",
        text="User prefers concise reports with citations.",
        source_run_id="run1",
        confidence=0.7,
    )
    assert await store.upsert_many([rec], org_id="org1") == 1
    found = await store.search("concise reports", org_id="org1")
    assert any(r.id == "m1" for r in found)

    # restart simulation: brand-new store instance, same DB
    store2 = SQLMemoryStore(session_factory)
    assert any(r.id == "m1" for r in await store2.recent(limit=5, org_id="org1"))
