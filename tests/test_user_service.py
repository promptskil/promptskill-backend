"""User service tests — Phase 8 — Step 8.3a.

Gate coverage:
  - get_user happy path returns {id, email}
  - get_user for nonexistent user → 404
  - update_email writes new email
  - update_email with duplicate (other user's email) → 409
  - update_email for nonexistent user → 404
  - update_email to same email is idempotent (no 409)
"""
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select

from app.models.user import User
from app.services.user_service import get_user, update_email


# ─────────────────────── fixtures ───────────────────────────────────────

@pytest_asyncio.fixture(loop_scope="session")
async def user_a(db_session):
    u = User(
        email=f"usr-a-{uuid4().hex[:8]}@t.local",
        password_hash="$2b$04$" + "a" * 53,
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest_asyncio.fixture(loop_scope="session")
async def user_b(db_session):
    u = User(
        email=f"usr-b-{uuid4().hex[:8]}@t.local",
        password_hash="$2b$04$" + "a" * 53,
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


# ─────────────────────── get_user ────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_get_user_returns_id_and_email(db_session, user_a):
    result = await get_user(user_a.id, db_session)
    assert result["id"] == user_a.id
    assert result["email"] == user_a.email


@pytest.mark.asyncio(loop_scope="session")
async def test_get_user_nonexistent_returns_404(db_session):
    with pytest.raises(HTTPException) as exc:
        await get_user(uuid4(), db_session)
    assert exc.value.status_code == 404


# ─────────────────────── update_email ────────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_update_email_writes_new_value(db_session, user_a):
    new_email = f"new-{uuid4().hex[:8]}@t.local"
    result = await update_email(user_a.id, new_email, db_session)
    assert result["id"] == user_a.id
    assert result["email"] == new_email

    # verify persistence
    refreshed = (
        await db_session.execute(select(User).where(User.id == user_a.id))
    ).scalar_one()
    assert refreshed.email == new_email


@pytest.mark.asyncio(loop_scope="session")
async def test_update_email_duplicate_returns_409(db_session, user_a, user_b):
    with pytest.raises(HTTPException) as exc:
        await update_email(user_a.id, user_b.email, db_session)
    assert exc.value.status_code == 409


@pytest.mark.asyncio(loop_scope="session")
async def test_update_email_nonexistent_user_returns_404(db_session):
    with pytest.raises(HTTPException) as exc:
        await update_email(uuid4(), "whoever@t.local", db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_update_email_same_email_is_idempotent(db_session, user_a):
    """Updating email to the same value must not raise 409.

    Postgres UNIQUE index allows UPDATE ... SET email = email since no
    second row with that value exists. Test guards against a future
    regression where someone adds a pre-check that would wrongly 409.
    """
    result = await update_email(user_a.id, user_a.email, db_session)
    assert result["email"] == user_a.email
