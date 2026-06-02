"""Owner-operated provisioning logic.

Used by scripts/create_admin.py and scripts/set_access.py. NOT exposed
over HTTP — admins and org access are owner-controlled only (one admin
per org, admin email reserved). Kept in a service so it is unit-testable.
"""
import secrets
from datetime import datetime, timedelta
from uuid import uuid4

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.business import Business, BusinessStatus
from app.models.business_member import BusinessMember
from app.models.reset_token import PasswordResetToken
from app.models.user import AccountType, User

# Admin onboarding link lives longer than a normal 1h reset so the owner
# has time to hand it over.
PROVISION_TOKEN_HOURS = 72


def _unusable_password_hash() -> str:
    """A valid bcrypt hash of a random secret — no one can match it, so the
    admin MUST set their password via the reset link. rounds=4 is fine: the
    hash is never verified (replaced on first reset)."""
    secret = secrets.token_urlsafe(32).encode()
    return bcrypt.hashpw(secret, bcrypt.gensalt(rounds=4)).decode()


def _reset_base_url() -> str:
    return (
        getattr(settings, "APP_BASE_URL", None)
        or "https://web-production-3a6e3.up.railway.app"
    )


async def provision_admin(
    email: str,
    org_name: str,
    db: AsyncSession,
    base_url: str | None = None,
) -> dict:
    """Create an admin account (account_type=admin, no usable password) + its
    org (status=active) + admin membership, plus a password-reset token.

    Returns the reset URL to hand to the admin. Raises ValueError if the
    email already exists (one-email-one-role).
    """
    email = email.lower().strip()

    existing = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"email already in use: {email}")

    user = User(
        email=email,
        password_hash=_unusable_password_hash(),
        account_type=AccountType.admin,
    )
    db.add(user)
    await db.flush()

    business = Business(
        owner_id=user.id,
        name=org_name,
        status=BusinessStatus.active,
    )
    db.add(business)
    await db.flush()

    db.add(
        BusinessMember(
            business_id=business.id, user_id=user.id, role="admin"
        )
    )

    token = str(uuid4())
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token=token,
            expires_at=datetime.utcnow()
            + timedelta(hours=PROVISION_TOKEN_HOURS),
        )
    )

    # Capture PKs before commit — a default-config session expires
    # attributes on commit, and re-loading them would be sync IO in an
    # async context (MissingGreenlet).
    user_id = user.id
    business_id = business.id
    await db.commit()

    base = base_url or _reset_base_url()
    return {
        "user_id": user_id,
        "business_id": business_id,
        "reset_token": token,
        "reset_url": f"{base}/auth/reset-password?token={token}",
    }


async def set_org_access(
    email: str,
    enabled: bool,
    db: AsyncSession,
) -> dict:
    """Enable/disable a business by its admin/owner email.

    Disabling sets status='disabled' — the admin AND all employees are
    blocked at login until re-enabled. Raises ValueError if the email is
    unknown or owns no business.
    """
    email = email.lower().strip()

    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is None:
        raise ValueError(f"no user with email: {email}")

    business = (
        await db.execute(
            select(Business).where(Business.owner_id == user.id)
        )
    ).scalar_one_or_none()
    if business is None:
        raise ValueError(f"{email} does not own a business")

    business.status = (
        BusinessStatus.active if enabled else BusinessStatus.disabled
    )
    business_id = business.id
    status_value = business.status.value
    await db.commit()
    return {"business_id": business_id, "status": status_value}
