"""Database infrastructure for Onsite48's authenticated product layer."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import CONFIG


class Base(DeclarativeBase):
    """Base class for all durable Onsite48 records."""


_database_url: Optional[str] = None
_engine = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def get_database_url() -> str:
    """Return the configured async PostgreSQL connection URL."""
    return CONFIG.database_url


def get_engine():
    """Create the process-wide async engine only when it is first needed."""
    global _engine, _database_url
    database_url = get_database_url()
    if _engine is None or database_url != _database_url:
        _database_url = database_url
        _engine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the reusable async SQLAlchemy session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency providing a transaction-scoped database session."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def initialize_database() -> None:
    """Create tables for local development; production should use Alembic migrations."""
    # Import registers all models on Base.metadata before create_all.
    from app import models  # noqa: F401

    async with get_engine().begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def close_database() -> None:
    """Dispose database connections during application shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


__all__ = [
    "Base",
    "get_db_session",
    "get_session_factory",
    "initialize_database",
    "close_database",
]