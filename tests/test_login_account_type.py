"""Login account-type + org-status tests (Phase 1 — one-email-one-role).

Covers the new login contract:
  - login returns account_type + business_id
  - individual → account_type='individual', business_id=None
  - admin/employee → their role + their business_id
  - a disabled org blocks BOTH admin and employee at login (403)
"""
from datetime import datetime
from uuid import uuid4

import bcrypt
import pytest
from fastapi import HTTPException

from app.models.business import Business, BusinessStatus
from app.models.business_member import BusinessMember
from app.models.user import AccountType, User
from app.services import auth_service

_PW = "password123"


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=4)).decode()


async def _make_user(db_session, account_type=AccountType.individual):
    u = User(
        email=f"acct-{uuid4().hex[:8]}@t.local",
        password_hash=_hash(_PW),
        account_type=account_type,
        email_verified_at=datetime.utcnow(),
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


async def _make_org(db_session, owner, status=BusinessStatus.active):
    b = Business(
        owner_id=owner.id,
        name=f"Org {uuid4().hex[:6]}",
        status=status,
    )
    db_session.add(b)
    await db_session.flush()
    db_session.add(
        BusinessMember(business_id=b.id, user_id=owner.id, role="admin")
    )
    await db_session.commit()
    await db_session.refresh(b)
    return b


async def _add_employee(db_session, business, status=BusinessStatus.active):
    emp = await _make_user(db_session, AccountType.employee)
    db_session.add(
        BusinessMember(
            business_id=business.id, user_id=emp.id, role="employee"
        )
    )
    await db_session.commit()
    return emp


@pytest.mark.asyncio(loop_scope="session")
async def test_individual_login_returns_individual_no_business(db_session):
    u = await _make_user(db_session, AccountType.individual)
    result = await auth_service.login(u.email, _PW, db_session)
    assert result["account_type"] == "individual"
    assert result["business_id"] is None
    assert len(result["token"]) > 100


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_login_returns_admin_and_business(db_session):
    u = await _make_user(db_session, AccountType.admin)
    b = await _make_org(db_session, u)
    result = await auth_service.login(u.email, _PW, db_session)
    assert result["account_type"] == "admin"
    assert result["business_id"] == b.id


@pytest.mark.asyncio(loop_scope="session")
async def test_employee_login_returns_employee_and_business(db_session):
    owner = await _make_user(db_session, AccountType.admin)
    b = await _make_org(db_session, owner)
    emp = await _add_employee(db_session, b)
    result = await auth_service.login(emp.email, _PW, db_session)
    assert result["account_type"] == "employee"
    assert result["business_id"] == b.id


@pytest.mark.asyncio(loop_scope="session")
async def test_disabled_org_blocks_admin_login(db_session):
    u = await _make_user(db_session, AccountType.admin)
    await _make_org(db_session, u, status=BusinessStatus.disabled)
    with pytest.raises(HTTPException) as exc:
        await auth_service.login(u.email, _PW, db_session)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "access_disabled"


@pytest.mark.asyncio(loop_scope="session")
async def test_disabled_org_blocks_employee_login(db_session):
    owner = await _make_user(db_session, AccountType.admin)
    b = await _make_org(db_session, owner, status=BusinessStatus.disabled)
    emp = await _add_employee(db_session, b)
    with pytest.raises(HTTPException) as exc:
        await auth_service.login(emp.email, _PW, db_session)
    assert exc.value.status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_wrong_password_returns_401(db_session):
    u = await _make_user(db_session, AccountType.individual)
    with pytest.raises(HTTPException) as exc:
        await auth_service.login(u.email, "wrong-password", db_session)
    assert exc.value.status_code == 401
