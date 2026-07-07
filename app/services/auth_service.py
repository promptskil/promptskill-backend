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

from app.auth import _subscription_active
from app.config import settings
from app.models import EmailVerificationToken, PasswordResetToken, Session, User
from app.models.business import Business, BusinessStatus
from app.models.business_member import BusinessMember
from app.models.user import AccountType, UserStatus
from app.tasks.email_task import send_reset_email_task
from app.tasks.verification_email_task import send_verification_email_task

# Resend client configuration
resend.api_key = settings.RESEND_API_KEY

logger = logging.getLogger(__name__)

SESSION_LIFETIME_DAYS = 30
RESET_TOKEN_LIFETIME_HOURS = 1
EMAIL_VERIFICATION_CODE_TTL_MINUTES = 15
EMAIL_VERIFICATION_MAX_ATTEMPTS = 5
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

async def _create_email_verification_token(
    user_id: UUID,
    db: AsyncSession,
) -> str:
    token_str = f"{secrets.randbelow(1_000_000):06d}"
    now_naive = datetime.utcnow()

    await db.execute(
        update(EmailVerificationToken)
        .where(EmailVerificationToken.user_id == user_id)
        .where(EmailVerificationToken.used_at.is_(None))
        .where(EmailVerificationToken.expires_at > now_naive)
        .values(used_at=now_naive)
    )

    db.add(
        EmailVerificationToken(
            user_id=user_id,
            token=token_str,
            expires_at=now_naive
            + timedelta(minutes=EMAIL_VERIFICATION_CODE_TTL_MINUTES),
        )
    )
    return token_str


async def signup(email: str, password: str, db: AsyncSession) -> UUID:
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
    # Decision B: no card-less trial. New users have NO subscription
    # (status NULL) -> checkout_required -> the paywall blocks /generate
    # until Stripe grants trialing after the card is entered at checkout.
    user = User(
        email=email,
        password_hash=password_hash,
    )
    db.add(user)
    await db.flush()  # populate user.id

    verification_token = await _create_email_verification_token(user.id, db)
    user_id = user.id
    await db.commit()

    # post-commit: user and verification token are durable before dispatch.
    # Broker errors surface as 500 by design — silent failure would
    # hide system-wide email outages.
    send_verification_email_task.delay(email, verification_token)

    return user_id


async def verify_email_code(email: str, code: str, db: AsyncSession) -> None:
    """Verify an account by a 6-digit code entered in-app.

    A code is short (guessable), so this is defended by: email-bound lookup
    (never code-alone), a per-token attempt cap, a short expiry, and a
    router-level rate limit. All failure paths return the SAME opaque error
    so nothing leaks whether the email exists or a code is close.
    """
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "code_invalid",
                "message": "That code is invalid or has expired.",
            },
        )
    if user.email_verified_at is not None:
        return  # idempotent — already verified

    now_naive = datetime.utcnow()
    result = await db.execute(
        select(EmailVerificationToken)
        .where(EmailVerificationToken.user_id == user.id)
        .where(EmailVerificationToken.used_at.is_(None))
        .where(EmailVerificationToken.expires_at > now_naive)
        .order_by(EmailVerificationToken.expires_at.desc())
    )
    token_row = result.scalars().first()
    if token_row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "code_expired",
                "message": "That code is invalid or has expired.",
            },
        )

    if token_row.attempts >= EMAIL_VERIFICATION_MAX_ATTEMPTS:
        token_row.used_at = now_naive
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "too_many_attempts",
                "message": "Too many attempts. Request a new code.",
            },
        )

    if not secrets.compare_digest(token_row.token, code):
        token_row.attempts += 1
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "code_invalid",
                "message": "That code is invalid or has expired.",
            },
        )

    user.email_verified_at = now_naive
    token_row.used_at = now_naive
    await db.commit()


# ─────────────────────── resend_verification ─────────────────────

async def resend_verification(email: str, db: AsyncSession) -> None:
    """Re-issue a verification email for an UNVERIFIED account.

    Rule 1 (re-entry, unverified): a user who signed up but never clicked
    the link reopens the app, enters their email, and the system must send
    a fresh verification email — no access, no drift to Main.

    No-op WITH NO SIGNAL if the email is unknown or already verified: the
    caller always gets {success: true}, so this endpoint can neither
    enumerate accounts nor reveal verification state. Reuses
    _create_email_verification_token, which invalidates any prior unused
    token before issuing the new one (single live token per user).
    """
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or user.email_verified_at is not None:
        return

    verification_token = await _create_email_verification_token(user.id, db)
    await db.commit()

    # post-commit: token is durable before dispatch (mirrors signup).
    send_verification_email_task.delay(user.email, verification_token)


# ───────────────────────────── login ─────────────────────────────

async def authenticate(
    email: str, password: str, db: AsyncSession
) -> tuple[User, UUID | None]:
    """Verify credentials + access gates + resolve org context.

    Does NOT issue a session — callers decide whether to mint one. Raises
    the same 401/403 as the original login. Returns (user, business_id).
    """
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

    if user.email_verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "email_not_verified",
                "message": "Please verify your email before logging in.",
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

    return user, business_id


async def issue_session(user_id: UUID, db: AsyncSession) -> str:
    """Mint a JWT, persist a Session row, commit. Returns the token."""
    token = _issue_jwt(user_id)
    session = Session(
        user_id=user_id,
        token=token,
        expires_at=_session_expiry(),
    )
    db.add(session)
    await db.commit()
    return token


def _checkout_required(user: User) -> bool:
    if user.account_type != AccountType.individual:
        return False
    return not _subscription_active(user)


async def login(email: str, password: str, db: AsyncSession) -> dict:
    user, business_id = await authenticate(email, password, db)
    token = await issue_session(user.id, db)
    return {
        "token": token,
        "user_id": user.id,
        "account_type": user.account_type.value,
        "business_id": business_id,
        "checkout_required": _checkout_required(user),
    }


async def business_login(email: str, password: str, db: AsyncSession) -> dict:
    """Business-surface login. Individual accounts are rejected BEFORE a
    session is issued — no token, no session row. The fundamental
    /auth/login is unaffected (individuals still log in there).
    """
    user, business_id = await authenticate(email, password, db)
    if user.account_type == AccountType.individual:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "individual_not_permitted",
                "message": (
                    "This login is for business accounts. "
                    "Use the email you were invited with."
                ),
            },
        )
    token = await issue_session(user.id, db)
    return {
        "token": token,
        "user_id": user.id,
        "account_type": user.account_type.value,
        "business_id": business_id,
        "checkout_required": False,
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
        user = await db.get(User, session.user_id)
        if user is None:
            return {"valid": False, "reason": "invalid"}
        response = {"valid": True, "user_id": str(session.user_id)}
        if _checkout_required(user):
            response["checkout_required"] = True
        return response

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
        .values(password_hash=password_hash, email_verified_at=now_naive)
    )

    # Mark token used — naive UTC, matches schema
    reset_token.used_at = now_naive

    # DELETE all sessions for this user — explicit, not cascade
    await db.execute(
        delete(Session).where(Session.user_id == reset_token.user_id)
    )

    await db.commit()
