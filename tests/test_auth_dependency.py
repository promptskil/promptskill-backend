"""Auth dependency tests — Phase 9 — Step 9.1b.

Directly exercises the three 401 paths in app/auth.py::get_current_user
that aren't already covered by happy-path protected-endpoint tests:

  1. No Authorization header         → HTTPBearer returns None        → 401
  2. Malformed / invalid JWT         → jwt.InvalidTokenError          → 401
  3. Valid JWT but no matching DB session (revoked/forged) → 401

Test via any protected endpoint — /user is the simplest since it takes
no body. Service-layer behavior is tested elsewhere; this file exists
only to pin the FastAPI dependency branches.
"""
import jwt
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from uuid import uuid4

from app.config import settings
from app.database import get_db
from app.main import app


@pytest_asyncio.fixture(loop_scope="session")
async def client(db_session):
    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ─────────────────────── 401 paths ──────────────────────────────────────

async def test_no_authorization_header_returns_401(client):
    """credentials is None path (auth.py lines 22-26)."""
    r = await client.get("/user")
    assert r.status_code == 401


async def test_malformed_jwt_returns_401(client):
    """jwt.InvalidTokenError path (auth.py lines 35-36)."""
    r = await client.get(
        "/user",
        headers={"Authorization": "Bearer not-a-real-jwt"},
    )
    assert r.status_code == 401


async def test_valid_jwt_but_no_session_returns_401(client):
    """Valid signature, no DB session row — forged or revoked token
    path (auth.py lines 47-52). Mints a JWT with our secret but
    never inserts a corresponding Session row."""
    forged = jwt.encode(
        {"user_id": str(uuid4())},
        settings.JWT_SECRET,
        algorithm="HS256",
    )
    r = await client.get(
        "/user",
        headers={"Authorization": f"Bearer {forged}"},
    )
    assert r.status_code == 401
