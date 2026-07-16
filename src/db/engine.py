"""
Async SQLAlchemy engine and session factory.

Reads DATABASE_URL from environment via pydantic-settings.
The engine uses asyncpg as the driver (postgresql+asyncpg://).

Usage:
    async with get_db_session() as session:
        result = await session.execute(select(Policy).where(...))
"""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

_log = logging.getLogger("db.engine")

# Module-level singletons — initialized lazily via _get_engine()
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_database_url() -> str:
    """
    Reads DATABASE_URL from environment. Converts postgres:// → postgresql+asyncpg://
    so callers don't need to remember the driver prefix.
    """
    import os
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to your .env file. "
            "Example: DATABASE_URL=postgresql+asyncpg://nivabupa:secret@localhost:5432/adjudication"
        )
    # Normalize legacy postgres:// scheme
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    # Ensure asyncpg driver is specified
    if url.startswith("postgresql://") and "asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _get_engine() -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        database_url = _get_database_url()
        _log.info("Creating async SQLAlchemy engine")
        _engine = create_async_engine(
            database_url,
            # NullPool is safe for async; avoids fork-safety issues in tests
            poolclass=NullPool,
            echo=False,
        )
        _session_factory = async_sessionmaker(
            _engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    _get_engine()  # Ensure engine is initialized
    assert _session_factory is not None
    return _session_factory


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager yielding a transactional database session.
    Commits on clean exit, rolls back on exception.
    """
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            yield session


async def create_all_tables() -> None:
    """
    Create all ORM-mapped tables. Used for dev/test only.
    Production should use Alembic migrations.
    """
    from db.models import Base
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    _log.info("All tables created via create_all_tables()")


async def dispose_engine() -> None:
    """Release connection pool resources. Call on application shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        _log.info("Async engine disposed")
