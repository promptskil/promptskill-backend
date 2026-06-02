"""Owner provisioning tests — admin_service (create_admin / set_access logic)."""
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.business import Business, BusinessStatus
from app.models.business_member import BusinessMember
from app.models.reset_token import PasswordResetToken
from app.models.user import AccountType, User
from app.services import admin_service


@pytest.mark.asyncio(loop_scope="session")
async def test_provision_admin_creates_account_org_and_reset(db_session):
    email = f"admin-{uuid4().hex[:8]}@org.test"
    result = await admin_service.provision_admin(
        email, "Acme Inc", db_session, base_url="https://x.test"
    )

    user = (
        await db_session.execute(select(User).where(User.email == email))
    ).scalar_one()
    assert user.account_type == AccountType.admin
    assert user.password_hash.startswith("$2b$")  # unusable but valid hash

    biz = (
        await db_session.execute(
            select(Business).where(Business.id == result["business_id"])
        )
    ).scalar_one()
    assert biz.owner_id == user.id
    assert biz.status == BusinessStatus.active

    member = (
        await db_session.execute(
            select(BusinessMember).where(
                BusinessMember.business_id == biz.id,
                BusinessMember.user_id == user.id,
            )
        )
    ).scalar_one()
    assert member.role == "admin"

    tok = (
        await db_session.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == user.id
            )
        )
    ).scalar_one()
    assert tok.expires_at > datetime.utcnow()
    expected = f"https://x.test/reset-password?token={tok.token}"
    assert result["reset_url"] == expected


@pytest.mark.asyncio(loop_scope="session")
async def test_provision_admin_rejects_existing_email(db_session):
    email = f"dup-{uuid4().hex[:8]}@org.test"
    await admin_service.provision_admin(email, "Org A", db_session)
    with pytest.raises(ValueError):
        await admin_service.provision_admin(email, "Org B", db_session)


@pytest.mark.asyncio(loop_scope="session")
async def test_set_org_access_toggles_status(db_session):
    email = f"adm-{uuid4().hex[:8]}@org.test"
    res = await admin_service.provision_admin(email, "Toggle Org", db_session)

    out = await admin_service.set_org_access(email, False, db_session)
    assert out["status"] == "disabled"
    biz = (
        await db_session.execute(
            select(Business).where(Business.id == res["business_id"])
        )
    ).scalar_one()
    assert biz.status == BusinessStatus.disabled

    out = await admin_service.set_org_access(email, True, db_session)
    assert out["status"] == "active"


@pytest.mark.asyncio(loop_scope="session")
async def test_set_org_access_unknown_email_raises(db_session):
    with pytest.raises(ValueError):
        await admin_service.set_org_access("ghost@org.test", False, db_session)
