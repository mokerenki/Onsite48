from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from typing import Any

import pytest


class RecordingFakeRouter:
    """Test router with the same keyword-compatible interface as production."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.calls: list[str] = []
        self._responses = responses or {}

    async def route(
        self,
        role: str,
        messages: Any,
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        self.calls.append(role)
        return self._responses.get(role, SimpleNamespace(content="{}"))


# ---------------------------------------------------------------------------
# Disposable database
# ---------------------------------------------------------------------------
# Database-backed tests are opt-in via SYNTHAI_TEST_DATABASE_URL so the default
# suite stays hermetic and fast. The release gate and CI set it.
#
# Each test that asks for `disposable_session_factory` gets its own uniquely
# named database, migrated from zero with Alembic and dropped afterwards, so a
# migration can never pass because some earlier run left a column behind. This
# is the only way to catch a migration that is not actually reversible from an
# empty database, which is the case that breaks production deploys.

TEST_DATABASE_URL_ENV = "SYNTHAI_TEST_DATABASE_URL"


def _admin_url() -> str:
    url = os.environ.get(TEST_DATABASE_URL_ENV)
    if not url:
        pytest.skip(
            f"set {TEST_DATABASE_URL_ENV} to run database-backed tests "
            "(the release gate does this)"
        )
    return url


def _database_name(prefix: str = "onsite48_test") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


async def _create_database(admin_url: str, name: str) -> str:
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    finally:
        await engine.dispose()
    # Swap the database component of the admin URL for the new one.
    base, _, _ = admin_url.rpartition("/")
    return f"{base}/{name}"


async def _drop_database(admin_url: str, name: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            # text() is required: exec_driver_sql does not bind :params, and an
            # unbound placeholder silently becomes a syntax error that leaves the
            # throwaway database behind.
            await connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ).bindparams(name=name)
            )
            await connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def disposable_database_url() -> str:
    """A freshly migrated, empty database, dropped on teardown."""
    admin_url = _admin_url()
    name = _database_name()
    url = await _create_database(admin_url, name)

    from alembic import command
    from alembic.config import Config as AlembicConfig

    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config = AlembicConfig(os.path.join(backend_root, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(backend_root, "alembic"))
    # env.py reads this variable, not the ini value, so that a test can never
    # redirect migrations to the placeholder host committed in alembic.ini.
    previous = os.environ.get("SYNTHAI_ALEMBIC_DATABASE_URL")
    os.environ["SYNTHAI_ALEMBIC_DATABASE_URL"] = url
    try:
        command.upgrade(config, "head")
        yield url
    finally:
        if previous is None:
            os.environ.pop("SYNTHAI_ALEMBIC_DATABASE_URL", None)
        else:
            os.environ["SYNTHAI_ALEMBIC_DATABASE_URL"] = previous
        await _drop_database(admin_url, name)


@pytest.fixture
async def disposable_session_factory(disposable_database_url: str):
    """An async_sessionmaker bound to a freshly migrated database."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(disposable_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()
