"""Sync DB engine for Celery worker process — Phase 4.4.

Why separate from app.database:
  Worker is a separate process running synchronously
  (full-stack-engineer L948-L954). The async engine cannot be used
  here — asyncpg pools are event-loop-bound and on_failure runs
  without an event loop.

Driver: psycopg2 (already in requirements.txt).
Pool:   small — worker concurrency is low, per-failure DLQ writes
        are rare relative to broker throughput.

URL normalization:
  Railway's DATABASE_URL may come as 'postgresql://' (plain) or
  'postgresql+asyncpg://' (if explicitly set for the async engine).
  This module coerces both to 'postgresql+psycopg2://' for sync use.
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_DATABASE_URL = os.getenv("DATABASE_URL")

if not _DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

# Normalize async → sync driver
if "+asyncpg" in _DATABASE_URL:
    _SYNC_URL = _DATABASE_URL.replace("+asyncpg", "+psycopg2")
elif _DATABASE_URL.startswith("postgresql://"):
    _SYNC_URL = _DATABASE_URL.replace(
        "postgresql://", "postgresql+psycopg2://", 1
    )
else:
    _SYNC_URL = _DATABASE_URL

sync_engine = create_engine(
    _SYNC_URL,
    pool_size=2,
    max_overflow=3,
    pool_pre_ping=True,  # survive broker-long idle disconnects
)

SessionLocal = sessionmaker(
    bind=sync_engine,
    autoflush=False,
    expire_on_commit=False,
)
