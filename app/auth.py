from datetime import datetime, timezone
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.cookies import SESSION_COOKIE_NAME, WEB_COOKIE_ORIGINS
from app.database import get_db
from app.models import Session as SessionModel
from app.models import User

bearer_scheme = HTTPBearer(auto_error=False)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    # Bearer first (mobile + current web); cookie fallback (web post-migration).
    if credentials is not None:
        token = credentials.credentials
    else:
        token = request.cookies.get(SESSION_COOKIE_NAME)
        # CSRF defense: the browser sends the cookie ambiently, so a
        # state-changing cookie-authed request must carry a trusted Origin.
        # Bearer auth (mobile/extension) isn't CSRF-able and is exempt.
        if token and request.method not in _SAFE_METHODS:
            if request.headers.get("origin") not in WEB_COOKIE_ORIGINS:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="CSRF origin check failed",
                )
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

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

    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    if user.email_verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "email_not_verified",
                "message": "Please verify your email before continuing.",
            },
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
        expires_at = user.subscription_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at > datetime.now(timezone.utc)
    return True


async def require_active_subscription(
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    """Gate paid features — an active/trialing subscription is required."""
    if not settings.PAYWALL_ENABLED:
        return user_id  # paywall disabled — no gating
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    if not _subscription_active(user):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Active subscription required",
        )
    return user_id
