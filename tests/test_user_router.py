"""User router HTTP-boundary tests — Phase 8 — Step 8.3b.

Scope: locks the ASGI boundary for GET /user and PATCH /user/email.
Service-layer semantics are covered in test_user_service.py.

Gate coverage:
  - GET /user happy path → 200 with {id, email}
  - PATCH /user/email happy path → 200 with new email
  - Both endpoints without auth → 401
  - PATCH /user/email with email already taken → 409
  - PATCH /user/email with malformed email → 400 (via 422→400 handler)
"""
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.main import app


# ─────────────────────── fixtures ───────────────────────────────────────

@pytest_asyncio.fixture(loop_scope="session")
async def client(db_session):
    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _signup(client, email):
    r = await client.post(
        "/auth/signup",
        json={"email": email, "password": "password123"},
    )
    assert r.status_code == 200, r.text
    b = r.json()
    return b["token"], b["user_id"]


# ─────────────────────── GET /user ──────────────────────────────────────

async def test_get_user_happy_path(client):
    email = f"getme-{uuid4().hex[:8]}@t.com"
    token, user_id = await _signup(client, email)

    r = await client.get(
        "/user",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == user_id
    assert body["email"] == email


async def test_get_user_no_auth_returns_401(client):
    r = await client.get("/user")
    assert r.status_code == 401


# ─────────────────────── PATCH /user/email ──────────────────────────────

async def test_update_email_happy_path(client):
    token, user_id = await _signup(client, f"old-{uuid4().hex[:8]}@t.com")
    new_email = f"new-{uuid4().hex[:8]}@t.com"

    r = await client.patch(
        "/user/email",
        json={"email": new_email},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == user_id
    assert body["email"] == new_email


async def test_update_email_no_auth_returns_401(client):
    r = await client.patch(
        "/user/email",
        json={"email": "whatever@t.com"},
    )
    assert r.status_code == 401


async def test_update_email_duplicate_returns_409(client):
    # user_b owns a particular email
    email_b = f"taken-{uuid4().hex[:8]}@t.com"
    await _signup(client, email_b)

    # user_a tries to steal it
    token_a, _ = await _signup(client, f"thief-{uuid4().hex[:8]}@t.com")
    r = await client.patch(
        "/user/email",
        json={"email": email_b},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 409, r.text


async def test_update_email_malformed_returns_400(client):
    token, _ = await _signup(client, f"bad-{uuid4().hex[:8]}@t.com")
    r = await client.patch(
        "/user/email",
        json={"email": "not-an-email"},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Project-wide 422 → 400 handler (app.exceptions.validation_exception_handler)
    assert r.status_code == 400, r.text
