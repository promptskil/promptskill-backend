"""Per-user access toggle tests — users.status (active | disabled).

Covers:
  - admin_service.set_user_access (service: toggle + unknown-email)
  - auth_service.login user-level gate (403 access_disabled) and its
    independence from the org-level businesses.status gate (precedence).
"""
from datetime import datetime
from uuid import uuid4

import bcrypt
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.business import Business, BusinessStatus
from app.models.business_member import BusinessMember
from app.models.user import AccountType, User, UserStatus
from app.services import admin_service, auth_service

_PW = "password123"


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=4)).decode()


async def _active_org(db_session):
    owner = User(
        email=f"own-{uuid4().hex[:8]}@t.local",
        password_hash=_hash(_PW),
        account_type=AccountType.admin,
        email_verified_at=datetime.utcnow(),
    )
    db_session.add(owner)
    await db_session.flush()
    biz = Business(
        owner_id=owner.id, name="Org", status=BusinessStatus.active
    )
    db_session.add(biz)
    await db_session.flush()
    db_session.add(
        BusinessMember(business_id=biz.id, user_id=owner.id, role="admin")
    )
    await db_session.commit()
    return owner, biz


async def _employee(db_session, biz, status=UserStatus.active):
    user = User(
        email=f"emp-{uuid4().hex[:8]}@t.local",
        password_hash=_hash(_PW),
        account_type=AccountType.employee,
        status=status,
        email_verified_at=datetime.utcnow(),
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        BusinessMember(business_id=biz.id, user_id=user.id, role="employee")
    )
    await db_session.commit()
    return user


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
    """Control — an active individual still logs in."""
    email = f"ind-{uuid4().hex[:8]}@t.local"
    await auth_service.signup(email, _PW, db_session)
    await _mark_verified(db_session, email)

    result = await auth_service.login(email, _PW, db_session)
    assert result["account_type"] == "individual"
    assert result["business_id"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_login_disabled_employee_org_active_403(db_session):
    """User gate is independent of the org — org active, user disabled."""
    _, biz = await _active_org(db_session)
    emp = await _employee(db_session, biz, status=UserStatus.disabled)

    with pytest.raises(HTTPException) as exc:
        await auth_service.login(emp.email, _PW, db_session)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "access_disabled"


@pytest.mark.asyncio(loop_scope="session")
async def test_login_active_employee_org_active_ok(db_session):
    """Control — active employee in an active org logs in with org context."""
    _, biz = await _active_org(db_session)
    emp = await _employee(db_session, biz, status=UserStatus.active)

    result = await auth_service.login(emp.email, _PW, db_session)
    assert result["account_type"] == "employee"
    assert result["business_id"] == biz.id


@pytest.mark.asyncio(loop_scope="session")
async def test_login_precedence_user_disabled_beats_org_active(db_session):
    """Precedence — a user-disabled employee stays blocked even though the
    org is active (i.e. re-enabling the org never un-blocks the user)."""
    _, biz = await _active_org(db_session)
    assert biz.status == BusinessStatus.active
    emp = await _employee(db_session, biz, status=UserStatus.disabled)

    with pytest.raises(HTTPException) as exc:
        await auth_service.login(emp.email, _PW, db_session)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "access_disabled"
