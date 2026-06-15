from datetime import datetime, timezone
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Session as SessionModel
from app.models import User
from app.models.user import AccountType

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    token = credentials.credentials

    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=["HS256"]
        )
        user_id = UUID(payload["user_id"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    result = await db.execute(
        select(SessionModel).where(
            SessionModel.token == token,
            SessionModel.expires_at > datetime.utcnow(),
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    request.state.user_id = user_id
    return user_id


ACTIVE_SUBSCRIPTION_STATUSES = {
    "active",
    "trialing",
    "grace_period",
    "billing_retry",
}


def _subscription_active(user: User) -> bool:
    """True if the user's subscription currently grants access.

    Status is the primary signal (webhook-maintained). expires_at is a
    fail-closed backstop: if it is set AND in the past, deny even when the
    status flag was not updated (missed cancel/expire webhook). If expires_at
    is None, trust the status.
    """
    if user.subscription_status not in ACTIVE_SUBSCRIPTION_STATUSES:
        return False
    if user.subscription_expires_at is not None:
        return user.subscription_expires_at > datetime.now(timezone.utc)
    return True


async def require_active_subscription(
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    """Gate paid features.

    Individual accounts need an active/trialing subscription. Business
    accounts (admin/employee) are governed by org status gates, not Stripe,
    so they bypass this paywall.
    """
    if not settings.PAYWALL_ENABLED:
        return user_id  # paywall disabled — no gating
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    if user.account_type != AccountType.individual:
        return user_id  # business accounts bypass the Stripe paywall
    if not _subscription_active(user):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Active subscription required",
        )
    return user_id
