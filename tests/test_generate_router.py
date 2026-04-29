"""Generate router HTTP-boundary tests — Phase 6 — Step 6.3.

Scope: locks the POST /generate ASGI boundary — auth, header extraction,
rate limit decorator, body validation, response shape. Service-layer
behavior is covered in test_generate_service.py.

Gate coverage (from /build-checklist Step 6.3):
  - Valid body + auth + x-app-version → 200
  - app_version written to DB record
  - Missing x-app-version → 400
  - No auth token → 401
  - Invalid model Literal → 400 (via 422→400 handler)
  - Empty topic / topic > 500 → 400
  - 61st request within hour → 429 (keyed on user_id)
"""
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import get_db
from app.main import app
from app.models.prompt import Prompt
from app.services.generate_service import load_model_registry

load_model_registry()


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


@pytest_asyncio.fixture(loop_scope="session")
async def auth_token(client):
    """Sign up a fresh user and return (token, user_id)."""
    r = await client.post(
        "/auth/signup",
        json={"email": "gen-router@test.com", "password": "password123"},
    )
    assert r.status_code == 200
    body = r.json()
    return body["token"], body["user_id"]


@pytest.fixture
def mock_anthropic_ok():
    """Patch AsyncAnthropic — messages.create returns canned text."""
    resp = MagicMock()
    resp.content = [MagicMock(text="router happy path output")]

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=resp)

    with patch(
        "app.services.model_clients.anthropic.AsyncAnthropic",
        return_value=fake_client,
    ):
        yield fake_client


# ─────────────────────── Gate 1: happy path ─────────────────────────────

async def test_valid_request_returns_200_and_writes_row(
    client, auth_token, mock_anthropic_ok, db_session
):
    token, user_id = auth_token
    r = await client.post(
        "/generate",
        json={"model": "claude", "topic": "router happy"},
        headers={
            "Authorization": f"Bearer {token}",
            "x-app-version": "1.2.3",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "prompt_id" in body and "prompt" in body
    assert body["prompt"] == "router happy path output"
    UUID(body["prompt_id"])

    # app_version landed in DB
    row = (
        await db_session.execute(
            select(Prompt).where(Prompt.id == UUID(body["prompt_id"]))
        )
    ).scalar_one()
    assert row.app_version == "1.2.3"
    assert row.user_id == UUID(user_id)
    assert row.system_prompt_version != "fallback"
    assert row.system_prompt_version.startswith("v")


# ─────────────────────── Gate 2: missing x-app-version → 400 ────────────

async def test_missing_x_app_version_returns_400(
    client, auth_token, mock_anthropic_ok
):
    token, _ = auth_token
    r = await client.post(
        "/generate",
        json={"model": "claude", "topic": "no header"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "missing_header"


# ─────────────────────── Gate 3: no auth → 401 ──────────────────────────

async def test_missing_auth_returns_401(client, mock_anthropic_ok):
    r = await client.post(
        "/generate",
        json={"model": "claude", "topic": "no auth"},
        headers={"x-app-version": "1.0.0"},
    )
    assert r.status_code == 401


# ─────────────────────── Gate 4: invalid model → 400 ────────────────────

async def test_invalid_model_returns_400(
    client, auth_token, mock_anthropic_ok
):
    token, _ = auth_token
    r = await client.post(
        "/generate",
        json={"model": "bogus", "topic": "ok"},
        headers={
            "Authorization": f"Bearer {token}",
            "x-app-version": "1.0.0",
        },
    )
    assert r.status_code == 400


# ─────────────────────── Gate 5: topic length bounds ────────────────────

async def test_empty_topic_returns_400(
    client, auth_token, mock_anthropic_ok
):
    token, _ = auth_token
    r = await client.post(
        "/generate",
        json={"model": "claude", "topic": ""},
        headers={
            "Authorization": f"Bearer {token}",
            "x-app-version": "1.0.0",
        },
    )
    assert r.status_code == 400


async def test_topic_too_long_returns_400(
    client, auth_token, mock_anthropic_ok
):
    token, _ = auth_token
    r = await client.post(
        "/generate",
        json={"model": "claude", "topic": "x" * 501},
        headers={
            "Authorization": f"Bearer {token}",
            "x-app-version": "1.0.0",
        },
    )
    assert r.status_code == 400


# ─────────────────────── Gate 6: 60/hour rate limit → 429 ───────────────

async def test_rate_limit_kicks_in_at_61st_request(
    client, auth_token, mock_anthropic_ok, rate_limit_enabled
):
    """Drive 60 requests through → all 200. 61st → 429.

    Uses rate_limit_enabled fixture which swaps slowapi storage to
    in-memory and re-enables the limiter (default autouse disables it).
    """
    token, _ = auth_token
    headers = {
        "Authorization": f"Bearer {token}",
        "x-app-version": "1.0.0",
    }
    body = {"model": "claude", "topic": "rl"}

    # Warm 60 successful hits
    for i in range(60):
        r = await client.post("/generate", json=body, headers=headers)
        assert r.status_code == 200, f"req {i} failed: {r.status_code}"

    # 61st must be 429
    r = await client.post("/generate", json=body, headers=headers)
    assert r.status_code == 429
