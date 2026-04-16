"""Sentry wiring tests — Phase 9 — Step 9.2.

Scope: lock the contract that /debug-sentry is NEVER registered unless
settings.DEBUG_SENTRY is True. Production Railway env must not set the
flag. This test ensures a developer can't accidentally expose it by
leaving a flag toggle in a PR.

Note: does not exercise real Sentry event routing — that requires a
live DSN and is the ops-verification step (you trigger /debug-sentry
against Railway after deploy and confirm the event lands in the
Sentry dashboard).
"""
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture(loop_scope="session")
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_debug_sentry_absent_by_default(client):
    """With DEBUG_SENTRY=False (test default), /debug-sentry must 404."""
    r = await client.get("/debug-sentry")
    assert r.status_code == 404
