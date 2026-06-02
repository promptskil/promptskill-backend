"""Invite flow tests — Phase 3 (employee-only, create-on-invite)."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import bcrypt
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.business import Business, BusinessStatus
from app.models.business_invite import BusinessInvite
from app.models.business_member import BusinessMember
from app.models.session import Session
from app.models.user import AccountType, User
from app.services import business_service

_PW = "password123"


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=4)).decode()


async def _admin_with_org(db_session):
    owner = User(
        email=f"own-{uuid4().hex[:8]}@t.local",
        password_hash=_hash(_PW),
        account_type=AccountType.admin,
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


async def _make_invite(db_session, biz, owner, email, hours=72):
    inv = BusinessInvite(
        business_id=biz.id,
        invited_by_id=owner.id,
        email=email,
        token=uuid4().hex,
        role="employee",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=hours),
    )
    db_session.add(inv)
    await db_session.commit()
    return inv


@pytest.mark.asyncio(loop_scope="session")
async def test_invite_member_rejects_admin_role(db_session):
    owner, biz = await _admin_with_org(db_session)
    with pytest.raises(HTTPException) as exc:
        await business_service.invite_member(
            biz.id, owner.id, "new@t.local", "admin", db_session
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio(loop_scope="session")
async def test_invite_member_rejects_existing_email(db_session):
    owner, biz = await _admin_with_org(db_session)
    with pytest.raises(HTTPException) as exc:
        await business_service.invite_member(
            biz.id, owner.id, owner.email, "employee", db_session
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio(loop_scope="session")
async def test_accept_invite_creates_employee_account(db_session):
    owner, biz = await _admin_with_org(db_session)
    email = f"emp-{uuid4().hex[:8]}@t.local"
    inv = await _make_invite(db_session, biz, owner, email)

    result = await business_service.accept_invite(inv.token, _PW, db_session)
    assert result["account_type"] == "employee"
    assert result["business_id"] == biz.id
    assert len(result["token"]) > 100

    user = (
        await db_session.execute(select(User).where(User.email == email))
    ).scalar_one()
    assert user.account_type == AccountType.employee

    member = (
        await db_session.execute(
            select(BusinessMember).where(BusinessMember.user_id == user.id)
        )
    ).scalar_one()
    assert member.role == "employee"

    sess = (
        await db_session.execute(
            select(Session).where(Session.user_id == user.id)
        )
    ).scalar_one()
    assert sess.token == result["token"]

    gone = (
        await db_session.execute(
            select(BusinessInvite).where(BusinessInvite.id == inv.id)
        )
    ).scalar_one_or_none()
    assert gone is None


@pytest.mark.asyncio(loop_scope="session")
async def test_accept_invite_expired_410(db_session):
    owner, biz = await _admin_with_org(db_session)
    inv = await _make_invite(
        db_session, biz, owner, f"x-{uuid4().hex[:8]}@t.local", hours=-1
    )
    with pytest.raises(HTTPException) as exc:
        await business_service.accept_invite(inv.token, _PW, db_session)
    assert exc.value.status_code == 410


@pytest.mark.asyncio(loop_scope="session")
async def test_accept_invite_email_claimed_409(db_session):
    owner, biz = await _admin_with_org(db_session)
    email = f"claim-{uuid4().hex[:8]}@t.local"
    inv = await _make_invite(db_session, biz, owner, email)
    db_session.add(
        User(
            email=email,
            password_hash=_hash(_PW),
            account_type=AccountType.individual,
        )
    )
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await business_service.accept_invite(inv.token, _PW, db_session)
    assert exc.value.status_code == 409


@pytest.mark.asyncio(loop_scope="session")
async def test_accept_invite_unknown_token_404(db_session):
    with pytest.raises(HTTPException) as exc:
        await business_service.accept_invite(uuid4().hex, _PW, db_session)
    assert exc.value.status_code == 404
