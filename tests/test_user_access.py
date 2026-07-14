"""Per-user access toggle tests — users.status (active | disabled).

Covers:
  - admin_service.set_user_access (service: toggle + unknown-email)
  - auth_service.login user-level gate (403 access_disabled)
"""
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.user import User
from app.services import admin_service, auth_service

_PW = "password123"


async def _mark_verified(db_session, email: str) -> None:
    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    user.email_verified_at = datetime.utcnow()
    await db_session.commit()


# ───────────────────────── service: set_user_access ─────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_set_user_access_toggles_status(db_session):
    email = f"ind-{uuid4().hex[:8]}@t.local"
    await auth_service.signup(email, _PW, db_session)

    off = await admin_service.set_user_access(email, False, db_session)
    assert off["status"] == "disabled"

    on = await admin_service.set_user_access(email, True, db_session)
    assert on["status"] == "active"
    assert on["user_id"] == off["user_id"]


@pytest.mark.asyncio(loop_scope="session")
async def test_set_user_access_unknown_email_raises(db_session):
    with pytest.raises(ValueError):
        await admin_service.set_user_access(
            f"ghost-{uuid4().hex[:8]}@t.local", False, db_session
        )


# ───────────────────────── login gate ───────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_login_disabled_individual_403(db_session):
    email = f"ind-{uuid4().hex[:8]}@t.local"
    await auth_service.signup(email, _PW, db_session)
    await _mark_verified(db_session, email)
    await admin_service.set_user_access(email, False, db_session)

    with pytest.raises(HTTPException) as exc:
        await auth_service.login(email, _PW, db_session)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "access_disabled"


@pytest.mark.asyncio(loop_scope="session")
async def test_login_active_individual_ok(db_session):
    """Control — an active account still logs in."""
    email = f"ind-{uuid4().hex[:8]}@t.local"
    await auth_service.signup(email, _PW, db_session)
    await _mark_verified(db_session, email)

    result = await auth_service.login(email, _PW, db_session)
    assert result["token"]
