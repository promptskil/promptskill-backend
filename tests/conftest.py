"""Shared pytest fixtures for auth service tests.

Strategy:
- SAVEPOINT rollback against real Postgres per Decision A1.
  Each test runs inside an outer transaction; the service's internal
  db.commit() commits the savepoint only, so the outer transaction
  rolls back cleanly at teardown. Zero state leaks between tests.
- BCRYPT_ROUNDS override to 4 per Decision B1.
  Cuts bcrypt from ~200ms/hash to ~1ms, keeps real bcrypt code path.
- resend.Emails.send mocked per Decision C1.
  Autouse — no test ever hits the real Resend API.
"""
import os
from pathlib import Path

# Load .env into os.environ before importing app modules (matches server startup).
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

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from unittest.mock import MagicMock, patch

from app.services import auth_service


# ─────────────────────── bcrypt speedup (autouse) ───────────────────────

@pytest.fixture(autouse=True)
def _fast_bcrypt(monkeypatch):
    """Decision B1 — cut bcrypt from 12 rounds to 4 for test speed."""
    monkeypatch.setattr(auth_service, "BCRYPT_ROUNDS", 4)


# ─────────────────────── Resend mock (autouse) ──────────────────────────

@pytest.fixture(autouse=True)
def mock_resend():
    """Decision C1 — patch Resend SDK so no test touches the network.

    Tests that want to verify email-send behavior can assert on the mock.
    Tests that want to simulate Resend failure can set .side_effect.

    Patching via auth_service path — resend module is shared, so this
    mock also applies to email_task.py in eager mode (Step 4.3).
    """
    with patch(
        "app.services.auth_service.resend.Emails.send",
        new=MagicMock(return_value={"id": "test-email-id"}),
    ) as m:
        yield m


# ─────────────────────── DLQ sink mocks (autouse) ───────────────────────

@pytest.fixture(autouse=True)
def _mock_dlq_sinks():
    """Phase 4.4 — eager-mode on_failure must not hit sync Postgres or
    real Redis.

    Why: sync DB writes bypass the async SAVEPOINT used by db_session
    and would leak rows across tests. Real Redis is not available in CI.
    Mocking at the sink-function boundary keeps on_failure logic under
    test; dedicated DLQ-behavior tests (Step 4.5) will replace these
    with assertive mocks.
    """
    with patch("app.tasks.email_task._dlq_postgres_insert") as pg, \
         patch("app.tasks.email_task._dlq_redis_push") as rd:
        yield pg, rd


# ─────────────────────── slowapi limiter (autouse disable) ─────────────

@pytest.fixture(autouse=True)
def _disable_rate_limiter():
    """Default: rate limiting OFF for tests.

    Why: prod limiter storage_uri is Upstash Redis (set at module import
    time). Tests don't have a broker, and most tests are not exercising
    rate-limit semantics. Keep the decorator present (so the code path
    stays covered) but short-circuit the check.

    Opt-in: tests that need to verify the 429 behavior use the
    `rate_limit_enabled` fixture which flips this back on with an
    in-memory storage backend and resets counters.
    """
    from app.rate_limit import limiter
    prior = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = prior


@pytest.fixture
def rate_limit_enabled():
    """Opt-in fixture — enables the limiter with in-memory storage.

    Use from tests that assert 429 behavior (Phase 6 — Step 6.3 gate).
    """
    from limits.storage import MemoryStorage
    from limits.strategies import MovingWindowRateLimiter

    from app.rate_limit import limiter

    prior_enabled = limiter.enabled
    prior_storage = limiter._storage
    prior_limiter = limiter._limiter

    mem = MemoryStorage()
    limiter._storage = mem
    limiter._limiter = MovingWindowRateLimiter(mem)
    limiter.enabled = True
    yield limiter
    limiter.enabled = prior_enabled
    limiter._storage = prior_storage
    limiter._limiter = prior_limiter


# ─────────────────────── MODEL_REGISTRY (session autouse) ──────────────

@pytest.fixture(scope="session", autouse=True)
def _load_model_registry_once():
    """Phase 5 — MODEL_REGISTRY must be populated before any /generate call.

    Why session-scoped autouse: in prod the @app.on_event("startup") hook
    in app/main.py fires at uvicorn boot. httpx.ASGITransport does NOT
    execute lifespan events by default, so tests that go through the
    ASGI app (test_history_router.py, test_generate_router.py) would
    otherwise see an empty MODEL_REGISTRY and raise KeyError.

    Previous pattern: module-level load_model_registry() call in each
    test file — fragile, made test-selection order matter. This fixture
    is the root-level fix: one call per pytest session, guaranteed
    before any test runs, regardless of which files pytest picks.
    """
    from app.services.generate_service import load_model_registry
    load_model_registry()


# ─────────────────────── Celery eager mode (session autouse) ────────────

@pytest.fixture(scope="session", autouse=True)
def _celery_eager():
    """Step 4.3 D4 — tasks run inline in tests, no worker/broker.

    Session-scoped + no restore: pytest process exits after session,
    nothing to restore to. Also safer for future parallel pytest runs.

    task_eager_propagates=False matches prod: API caller never sees
    worker-level failures; DLQ (Step 4.4) + Sentry surface them
    to operators instead.
    """
    from app.tasks.celery_app import celery_app
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False
    yield


# ─────────────────────── DB engine (session-scoped) ─────────────────────

@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def db_engine():
    """Session-scoped engine built inside pytest's session loop.

    Fundamental: asyncpg's connection pool is loop-scoped. The prod
    engine in app.database binds to uvicorn's loop at import time;
    reusing it here would cross loop boundaries per test and trigger
    `InterfaceError: another operation is in progress`. Building the
    engine inside this fixture — which runs on the pytest session
    loop — keeps pool and tests on one loop for the whole run.
    """
    eng = create_async_engine(
        os.environ["DATABASE_URL"],
        pool_size=1,
        max_overflow=0,
    )
    yield eng
    await eng.dispose()


# ─────────────────────── DB session (SAVEPOINT) ─────────────────────────

@pytest_asyncio.fixture(loop_scope="session")
async def db_session(db_engine):
    """Decision A1 — SAVEPOINT rollback per test against real Postgres.

    Pattern:
      1. open a connection from the session-scoped engine
      2. begin an outer transaction on it
      3. bind an AsyncSession in savepoint mode — service's .commit()
         only commits the savepoint, not the outer transaction
      4. rollback the outer transaction at teardown ⇒ DB clean
    """
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()
