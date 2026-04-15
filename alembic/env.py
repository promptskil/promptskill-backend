import asyncio
import os
from logging.config import fileConfig
from pathlib import Path

# Load .env into os.environ before importing app modules — mirrors
# conftest.py pattern. Required because alembic runs from a bare
# PowerShell/bash shell that does not inherit project env vars;
# app.database raises if DATABASE_URL is missing at import.
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for _raw in _env_path.read_text().splitlines():
        _line = _raw.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        _v = _v.strip()
        if len(_v) >= 2 and _v[0] == _v[-1] and _v[0] in ('"', "'"):
            _v = _v[1:-1]
        os.environ.setdefault(_k.strip(), _v)

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import DATABASE_URL
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = create_async_engine(
        DATABASE_URL,
        poolclass=pool.NullPool,
    )

    def do_run_migrations(connection):
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()

    async def run_async_migrations():
        async with connectable.connect() as conn:
            await conn.run_sync(do_run_migrations)

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
