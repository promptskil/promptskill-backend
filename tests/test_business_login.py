"""Business-surface login tests (auth_service.business_login).

Verifies the additive business-login layer:
  - individual -> 403 individual_not_permitted, and NO session row is minted
  - admin / employee -> succeed with token + business_id
  - credential + org-status gates are preserved (inherited from authenticate)
  - the fundamental auth_service.login still admits individuals (regression)
"""
from uuid import uuid4

import bcrypt
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import Session
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
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


async def _make_org(db_session, owner, status=BusinessStatus.active):
    b = Business(owner_id=owner.id, name=f"Org {uuid4().hex[:6]}", status=status)
    db_session.add(b)
    await db_session.flush()
    db_session.add(
        BusinessMember(business_id=b.id, user_id=owner.id, role="admin")
    )
    await db_session.commit()
    await db_session.refresh(b)
    return b


async def _add_employee(db_session, business):
    emp = await _make_user(db_session, AccountType.employee)
    db_session.add(
        BusinessMember(
            business_id=business.id, user_id=emp.id, role="employee"
        )
    )
    await db_session.commit()
    return emp


async def _session_count(db_session) -> int:
    return (
        await db_session.execute(select(func.count()).select_from(Session))
    ).scalar()


@pytest.mark.asyncio(loop_scope="session")
async def test_business_login_rejects_individual_no_session(db_session):
    u = await _make_user(db_session, AccountType.individual)
    before = await _session_count(db_session)
    with pytest.raises(HTTPException) as exc:
        await auth_service.business_login(u.email, _PW, db_session)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "individual_not_permitted"
    # Rejected BEFORE issue_session: no session row minted.
    assert await _session_count(db_session) == before


@pytest.mark.asyncio(loop_scope="session")
async def test_business_login_allows_admin(db_session):
    u = await _make_user(db_session, AccountType.admin)
    b = await _make_org(db_session, u)
    result = await auth_service.business_login(u.email, _PW, db_session)
    assert result["account_type"] == "admin"
    assert result["business_id"] == b.id
    assert len(result["token"]) > 100


@pytest.mark.asyncio(loop_scope="session")
async def test_business_login_allows_employee(db_session):
    owner = await _make_user(db_session, AccountType.admin)
    b = await _make_org(db_session, owner)
    emp = await _add_employee(db_session, b)
    result = await auth_service.business_login(emp.email, _PW, db_session)
    assert result["account_type"] == "employee"
    assert result["business_id"] == b.id


@pytest.mark.asyncio(loop_scope="session")
async def test_business_login_wrong_password_401(db_session):
    u = await _make_user(db_session, AccountType.admin)
    await _make_org(db_session, u)
    with pytest.raises(HTTPException) as exc:
        await auth_service.business_login(u.email, "wrong-password", db_session)
    assert exc.value.status_code == 401


@pytest.mark.asyncio(loop_scope="session")
async def test_business_login_disabled_org_403(db_session):
    u = await _make_user(db_session, AccountType.admin)
    await _make_org(db_session, u, status=BusinessStatus.disabled)
    with pytest.raises(HTTPException) as exc:
        await auth_service.business_login(u.email, _PW, db_session)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "access_disabled"


@pytest.mark.asyncio(loop_scope="session")
async def test_fundamental_login_still_admits_individual(db_session):
    """Regression: /auth/login (the fundamental) is unchanged -- individuals
    still log in there."""
    u = await _make_user(db_session, AccountType.individual)
    result = await auth_service.login(u.email, _PW, db_session)
    assert result["account_type"] == "individual"
    assert result["business_id"] is None
    assert len(result["token"]) > 100
