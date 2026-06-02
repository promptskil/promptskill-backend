import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import bcrypt
import jwt
import resend
from fastapi import HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import PasswordResetToken, Session, User
from app.models.business import Business, BusinessStatus
from app.models.business_member import BusinessMember
from app.models.user import AccountType, UserStatus
from app.tasks.email_task import send_reset_email_task
from app.tasks.welcome_email_task import send_welcome_email_task

# Resend client configuration
resend.api_key = settings.RESEND_API_KEY

logger = logging.getLogger(__name__)

SESSION_LIFETIME_DAYS = 30
RESET_TOKEN_LIFETIME_HOURS = 1
BCRYPT_ROUNDS = 12  # Production value; tests monkeypatch to 4 for speed.


# ──────────────────────────── helpers ────────────────────────────

async def _hash_password(password: str) -> str:
    """Offload bcrypt (100-250ms CPU at rounds=12) to threadpool."""
    hashed = await asyncio.to_thread(
        bcrypt.hashpw, password.encode(), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    )
    return hashed.decode()


async def _verify_password(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(
        bcrypt.checkpw, password.encode(), password_hash.encode()
    )


def _issue_jwt(user_id) -> str:
    """JWT exp MUST be aware UTC — PyJWT calls .timestamp() which
    misinterprets naive datetimes as local time.

    jti (RFC 7519): unique per-issuance identifier. Prevents token
    collision when two tokens are issued in the same second for the
    same user (e.g. signup→login within one request cycle). Also
    enables precise revocation if needed.
    """
    return jwt.encode(
        {
            "user_id": str(user_id),
            "exp": datetime.now(timezone.utc)
                   + timedelta(days=SESSION_LIFETIME_DAYS),
            "jti": secrets.token_urlsafe(16),
        },
        settings.JWT_SECRET,
        algorithm="HS256",
    )


def _session_expiry() -> datetime:
    """Naive UTC — matches sa.DateTime() (no tz) in sessions.expires_at."""
    return datetime.utcnow() + timedelta(days=SESSION_LIFETIME_DAYS)


# ──────────────────────────── signup ─────────────────────────────

async def signup(email: str, password: str, db: AsyncSession) -> tuple[str, UUID]:
    existing = await db.execute(
        select(User).where(User.email == email)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "email_exists",
                "message": "An account with this email already exists",
            },
        )

    password_hash = await _hash_password(password)
    user = User(email=email, password_hash=password_hash)
    db.add(user)
    await db.flush()  # populate user.id

    token = _issue_jwt(user.id)
    session = Session(
        user_id=user.id,
        token=token,
        expires_at=_session_expiry(),
    )
    db.add(session)
    await db.commit()

    # post-commit: user and session are durable before email dispatch.
    # Broker errors surface as 500 by design — silent failure would
    # hide system-wide email outages.
    send_welcome_email_task.delay(email)

    return token, user.id


# ───────────────────────────── login ─────────────────────────────

async def login(email: str, password: str, db: AsyncSession) -> dict:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    # Uniform 401 — email enumeration prevention (spec line 1178)
    if not user or not await _verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "invalid_credentials",
                "message": "Incorrect email or password",
            },
        )

    # User-level access gate — applies to every account_type
    # (individual | admin | employee). Checked after credential
    # verification (so it never leaks account existence) and BEFORE the
    # org gate. Independent of businesses.status: both must be active to
    # log in, and re-enabling an org never un-blocks a disabled user.
    if user.status == UserStatus.disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "access_disabled",
                "message": (
                    "This account's access is disabled. "
                    "Contact support at support@vaineai.com."
                ),
            },
        )

    # Resolve org context for business accounts; enforce suspension.
    business_id = None
    if user.account_type in (AccountType.admin, AccountType.employee):
        membership = (
            await db.execute(
                select(BusinessMember).where(
                    BusinessMember.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if membership is not None:
            business = (
                await db.execute(
                    select(Business).where(
                        Business.id == membership.business_id
                    )
                )
            ).scalar_one_or_none()
            if (
                business is not None
                and business.status == BusinessStatus.disabled
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error": "access_disabled",
                        "message": (
                            "Access to this organization is disabled. "
                            "Contact your owner."
                        ),
                    },
                )
            business_id = membership.business_id

    token = _issue_jwt(user.id)
    session = Session(
        user_id=user.id,
        token=token,
        expires_at=_session_expiry(),
    )
    db.add(session)
    await db.commit()
    return {
        "token": token,
        "user_id": user.id,
        "account_type": user.account_type.value,
        "business_id": business_id,
    }


# ──────────────────────────── validate ───────────────────────────

async def validate_token(token: str, db: AsyncSession) -> dict:
    """Returns 200 in both cases. Does NOT use auth middleware."""
    now_naive = datetime.utcnow()

    result = await db.execute(
        select(Session).where(
            Session.token == token,
            Session.expires_at > now_naive,
        )
    )
    session = result.scalar_one_or_none()
    if session:
        return {"valid": True, "user_id": str(session.user_id)}

    expired = await db.execute(
        select(Session).where(Session.token == token)
    )
    if expired.scalar_one_or_none():
        return {"valid": False, "reason": "expired"}
    return {"valid": False, "reason": "invalid"}


# ──────────────────────────── logout ─────────────────────────────

async def logout(token: str, db: AsyncSession) -> None:
    """Explicit DELETE, not cascade. User record preserved."""
    await db.execute(delete(Session).where(Session.token == token))
    await db.commit()


# ───────────────────────── forgot_password ───────────────────────

async def forgot_password(email: str, db: AsyncSession) -> None:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    # Email enumeration: 404 on not found (known gap, spec line 1212 locked)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "user_not_found",
                "message": "No account found with that email",
            },
        )

    token_str = str(uuid4())
    now_naive = datetime.utcnow()

    # Invalidate all prior unused reset tokens for this user
    await db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id)
        .where(PasswordResetToken.used_at.is_(None))
        .where(PasswordResetToken.expires_at > now_naive)
        .values(used_at=now_naive)
    )

    new_token = PasswordResetToken(
        user_id=user.id,
        token=token_str,
        expires_at=now_naive + timedelta(hours=RESET_TOKEN_LIFETIME_HOURS),
    )
    db.add(new_token)
    await db.commit()

    # post-commit: never dispatch for uncommitted tokens (prevents ghost emails)
    # Phase 4: retry + DLQ handled inside the task; broker errors surface as 500
    # by design — silent broker failure would hide system-wide email outages.
    send_reset_email_task.delay(user.email, token_str)


# ───────────────────────── reset_password ────────────────────────

async def reset_password(
    token: str, new_password: str, db: AsyncSession
) -> None:
    result = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token == token
        )
    )
    reset_token = result.scalar_one_or_none()

    if not reset_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "token_invalid",
                "message": "This reset link is invalid.",
            },
        )

    # Python `is not None` — ORM attribute, not SQL operator
    if reset_token.used_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "token_used",
                "message": "This reset link has already been used. "
                           "Request a new one.",
            },
        )

    now_naive = datetime.utcnow()
    if reset_token.expires_at < now_naive:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "token_expired",
                "message": "This reset link has expired. "
                           "Request a new one.",
            },
        )

    password_hash = await _hash_password(new_password)

    await db.execute(
        update(User)
        .where(User.id == reset_token.user_id)
        .values(password_hash=password_hash)
    )

    # Mark token used — naive UTC, matches schema
    reset_token.used_at = now_naive

    # DELETE all sessions for this user — explicit, not cascade
    await db.execute(
        delete(Session).where(Session.user_id == reset_token.user_id)
    )

    await db.commit()
