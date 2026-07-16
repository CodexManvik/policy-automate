"""
Alembic environment script — configures the async SQLAlchemy engine
and exposes the ORM metadata for auto-generated migrations.

Supports both online (live DB connection) and offline (SQL script generation) modes.
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Ensure src/ is importable from this env.py
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db.models import Base  # noqa: E402  — must be after sys.path modification

# Alembic Config object
config = context.config

# Override sqlalchemy.url with the DATABASE_URL environment variable.
# This keeps credentials out of alembic.ini.
_db_url = os.getenv("DATABASE_URL", "")
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url)

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ORM metadata target for --autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without an active DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations using asyncpg in online mode."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
