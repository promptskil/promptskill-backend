"""History router HTTP-boundary tests — Phase 7 — Step 7.3.

Scope: locks the ASGI boundary for PATCH /user/feedback, GET /history,
and PATCH /prompts/{id}/delete. Service-layer semantics are covered in
test_history_service.py.

Gate coverage (from /build-checklist Step 7.3):
  - PATCH /user/feedback happy path → 200
  - GET /history happy path → 200
  - PATCH /prompts/{id}/delete happy path → 200
  - No auth → 401 on all three
  - Cross-user prompt_id → 404 (feedback + delete)
  - Malformed UUID in path → 400 (via 422→400 handler)
  - limit > 50 → 400
  - history returns empty for new user
"""
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.main import app
from tests.auth_helpers import signup_verify_login


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


async def _signup(client, db_session, email):
    return await signup_verify_login(client, db_session, email)


async def _generate_prompt(client, token):
    """Route via /generate to create a real prompt row."""
    from unittest.mock import AsyncMock, MagicMock, patch
    resp = MagicMock()
    resp.content = [MagicMock(text="history-router test prompt")]
    fake = MagicMock()
    fake.messages.create = AsyncMock(return_value=resp)
    with patch(
        "app.services.model_clients.anthropic.AsyncAnthropic",
        return_value=fake,
    ):
        r = await client.post(
            "/generate",
            json={"model": "claude", "topic": "history router"},
            headers={
                "Authorization": f"Bearer {token}",
                "x-app-version": "1.0.0",
            },
        )
    assert r.status_code == 200, r.text
    return r.json()["prompt_id"]


# ─────────────────────── feedback ───────────────────────────────────────

async def test_feedback_happy_path(client, db_session):
    token, _ = await _signup(client, db_session, "fb-happy@t.com")
    prompt_id = await _generate_prompt(client, token)

    r = await client.patch(
        "/user/feedback",
        json={"prompt_id": prompt_id, "vote": "up"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["prompt_id"] == prompt_id
    assert body["vote"] == "up"


async def test_feedback_no_auth_returns_401(client):
    r = await client.patch(
        "/user/feedback",
        json={"prompt_id": str(uuid4()), "vote": "up"},
    )
    assert r.status_code == 401


async def test_feedback_cross_user_returns_404(client, db_session):
    t_a, _ = await _signup(client, db_session, "fb-a@t.com")
    t_b, _ = await _signup(client, db_session, "fb-b@t.com")
    prompt_id = await _generate_prompt(client, t_a)

    r = await client.patch(
        "/user/feedback",
        json={"prompt_id": prompt_id, "vote": "up"},
        headers={"Authorization": f"Bearer {t_b}"},
    )
    assert r.status_code == 404


async def test_feedback_invalid_vote_returns_400(client, db_session):
    token, _ = await _signup(client, db_session, "fb-invalid@t.com")
    prompt_id = await _generate_prompt(client, token)

    r = await client.patch(
        "/user/feedback",
        json={"prompt_id": prompt_id, "vote": "maybe"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400


# ─────────────────────── history ────────────────────────────────────────

async def test_history_happy_path(client, db_session):
    token, _ = await _signup(client, db_session, "h-happy@t.com")
    await _generate_prompt(client, token)
    await _generate_prompt(client, token)

    r = await client.get(
        "/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 2
    assert len(body["items"]) >= 2
    assert body["limit"] == 20
    assert body["offset"] == 0


async def test_history_no_auth_returns_401(client):
    r = await client.get("/history")
    assert r.status_code == 401


async def test_history_empty_user(client, db_session):
    token, _ = await _signup(client, db_session, "h-empty@t.com")
    r = await client.get(
        "/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_history_limit_over_50_returns_400(client, db_session):
    token, _ = await _signup(client, db_session, "h-limit@t.com")
    r = await client.get(
        "/history?limit=51",
        headers={"Authorization": f"Bearer {token}"},
    )
    # FastAPI Query(le=50) raises 422 → 400 via handler
    assert r.status_code == 400


# ─────────────────────── delete ─────────────────────────────────────────

async def test_delete_happy_path(client, db_session):
    token, _ = await _signup(client, db_session, "d-happy@t.com")
    prompt_id = await _generate_prompt(client, token)

    r = await client.patch(
        f"/prompts/{prompt_id}/delete",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["prompt_id"] == prompt_id
    assert body["deleted_at"] is not None

    # history excludes it
    r2 = await client.get(
        "/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    ids = [i["prompt_id"] for i in r2.json()["items"]]
    assert prompt_id not in ids


async def test_delete_no_auth_returns_401(client):
    r = await client.patch(f"/prompts/{uuid4()}/delete")
    assert r.status_code == 401


async def test_delete_cross_user_returns_404(client, db_session):
    t_a, _ = await _signup(client, db_session, "d-a@t.com")
    t_b, _ = await _signup(client, db_session, "d-b@t.com")
    prompt_id = await _generate_prompt(client, t_a)

    r = await client.patch(
        f"/prompts/{prompt_id}/delete",
        headers={"Authorization": f"Bearer {t_b}"},
    )
    assert r.status_code == 404


async def test_delete_malformed_uuid_returns_400(client, db_session):
    token, _ = await _signup(client, db_session, "d-bad@t.com")
    r = await client.patch(
        "/prompts/not-a-uuid/delete",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400


async def test_double_delete_returns_404(client, db_session):
    token, _ = await _signup(client, db_session, "d-double@t.com")
    prompt_id = await _generate_prompt(client, token)

    r1 = await client.patch(
        f"/prompts/{prompt_id}/delete",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 200
    r2 = await client.patch(
        f"/prompts/{prompt_id}/delete",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 404
