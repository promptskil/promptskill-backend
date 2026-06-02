"""Auth service regression tests.

Scope: locks Phase 3 service-layer behavior so Phase 4 Celery migration
has a pass/fail criterion. All tests run against real Postgres via
SAVEPOINT rollback (see conftest.py).

Test inventory (13 functions, D1 minimal + 1 parametrized reset rejection):
  1.  test_signup_happy_path
  2.  test_signup_duplicate_email_409
  3.  test_login_happy_path
  4.  test_login_invalid_credentials (parametrized: missing user, wrong pw)
  5.  test_validate_valid_token
  6.  test_validate_expired_session
  7.  test_validate_invalid_token
  8.  test_logout_deletes_session
  9.  test_forgot_password_happy_invalidates_prior
  10. test_forgot_password_missing_user_404
  11. test_forgot_password_dispatch_failure_does_not_propagate
  12. test_reset_password_full_cascade
  13. test_reset_password_rejects_bad_token (parametrized: invalid, used, expired)
"""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
import resend
from fastapi import HTTPException
from sqlalchemy import select, update

from app.models import PasswordResetToken, Session, User
from app.services import auth_service


# ─────────────────────── signup ─────────────────────────────────────────

async def test_signup_happy_path(db_session):
    token, user_id = await auth_service.signup(
        "signup-happy@test.com", "password123", db_session
    )
    assert len(token) > 100  # JWT shape check

    # User row persisted
    result = await db_session.execute(
        select(User).where(User.email == "signup-happy@test.com")
    )
    user = result.scalar_one()
    assert user.id == user_id
    assert user.password_hash.startswith("$2b$")  # bcrypt marker

    # Session row persisted with the issued token
    result = await db_session.execute(
        select(Session).where(Session.token == token)
    )
    session = result.scalar_one()
    assert session.user_id == user_id
    assert session.expires_at > datetime.utcnow()


async def test_signup_duplicate_email_409(db_session):
    await auth_service.signup("dup@test.com", "password123", db_session)
    with pytest.raises(HTTPException) as exc_info:
        await auth_service.signup("dup@test.com", "password123", db_session)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "email_exists"


# ─────────────────────── login ──────────────────────────────────────────

async def test_login_happy_path(db_session):
    _, signup_user_id = await auth_service.signup(
        "login@test.com", "password123", db_session
    )
    result = await auth_service.login(
        "login@test.com", "password123", db_session
    )
    assert result["user_id"] == signup_user_id
    assert len(result["token"]) > 100
    assert result["account_type"] == "individual"
    assert result["business_id"] is None

    # Two sessions now exist — signup + login
    result = await db_session.execute(
        select(Session).where(Session.user_id == signup_user_id)
    )
    assert len(result.scalars().all()) == 2


@pytest.mark.parametrize(
    "email,password,setup_user",
    [
        ("ghost@test.com", "password123", False),  # missing user
        ("exists@test.com", "wrong-password", True),  # wrong password
    ],
    ids=["missing_user", "wrong_password"],
)
async def test_login_invalid_credentials(
    db_session, email, password, setup_user
):
    """Uniform 401 response — spec line 1178, email enumeration prevention."""
    if setup_user:
        await auth_service.signup(email, "password123", db_session)
    with pytest.raises(HTTPException) as exc_info:
        await auth_service.login(email, password, db_session)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"] == "invalid_credentials"


# ─────────────────────── validate ───────────────────────────────────────

async def test_validate_valid_token(db_session):
    token, user_id = await auth_service.signup(
        "val@test.com", "password123", db_session
    )
    result = await auth_service.validate_token(token, db_session)
    assert result == {"valid": True, "user_id": str(user_id)}


async def test_validate_expired_session(db_session):
    token, _ = await auth_service.signup(
        "exp@test.com", "password123", db_session
    )
    # Age the session directly in DB
    await db_session.execute(
        update(Session)
        .where(Session.token == token)
        .values(expires_at=datetime.utcnow() - timedelta(days=1))
    )
    await db_session.commit()

    result = await auth_service.validate_token(token, db_session)
    assert result == {"valid": False, "reason": "expired"}


async def test_validate_invalid_token(db_session):
    result = await auth_service.validate_token(
        "not-a-real-jwt-token", db_session
    )
    assert result == {"valid": False, "reason": "invalid"}


# ─────────────────────── logout ─────────────────────────────────────────

async def test_logout_deletes_session(db_session):
    token, user_id = await auth_service.signup(
        "out@test.com", "password123", db_session
    )
    await auth_service.logout(token, db_session)

    result = await db_session.execute(
        select(Session).where(Session.token == token)
    )
    assert result.scalar_one_or_none() is None

    # User row preserved (not cascade-deleted)
    user_result = await db_session.execute(
        select(User).where(User.id == user_id)
    )
    assert user_result.scalar_one() is not None


# ─────────────────────── forgot_password ────────────────────────────────

async def test_forgot_password_happy_invalidates_prior(db_session, mock_resend):
    await auth_service.signup("forgot@test.com", "password123", db_session)
    # Reset mock after signup — signup now sends a welcome email.
    # This test asserts only on forgot_password dispatch behavior.
    mock_resend.reset_mock()

    # First call — creates token A (used_at=None)
    await auth_service.forgot_password("forgot@test.com", db_session)
    # Second call — invalidates token A, creates token B (used_at=None)
    await auth_service.forgot_password("forgot@test.com", db_session)

    result = await db_session.execute(
        select(PasswordResetToken)
        .join(User, User.id == PasswordResetToken.user_id)
        .where(User.email == "forgot@test.com")
        .order_by(PasswordResetToken.expires_at)
    )
    tokens = result.scalars().all()
    assert len(tokens) == 2
    assert tokens[0].used_at is not None, "prior token should be invalidated"
    assert tokens[1].used_at is None, "latest token should be active"

    # Resend was invoked twice — once per forgot_password call
    assert mock_resend.call_count == 2


async def test_forgot_password_missing_user_404(db_session, mock_resend):
    """Known email-enumeration gap per spec line 1212 — returns 404 on miss."""
    with pytest.raises(HTTPException) as exc_info:
        await auth_service.forgot_password("ghost@test.com", db_session)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"] == "user_not_found"
    assert mock_resend.call_count == 0  # no email attempt on missing user


async def test_forgot_password_dispatch_failure_does_not_propagate(
    db_session, mock_resend
):
    """Phase 4 — Resend failure in the Celery task path must not
    surface as 500 to the dispatch caller (auth_service.forgot_password).
    Token must still be persisted; DLQ sinks (mocked via autouse
    _mock_dlq_sinks) handle observability downstream.

    Sink-level behavior (retry count, DLQ writes, Sentry gate, log
    redaction) is covered in tests/test_email_task.py — this test
    asserts only the auth_service boundary contract."""
    mock_resend.side_effect = resend.exceptions.ResendError(
        code=401,
        message="API key is invalid",
        suggested_action="Use a valid API key",
        error_type="authentication_error",
    )
    await auth_service.signup("rerr@test.com", "password123", db_session)

    # Must NOT raise despite Resend failure
    await auth_service.forgot_password("rerr@test.com", db_session)

    # Token row must still exist and be unused
    result = await db_session.execute(
        select(PasswordResetToken)
        .join(User, User.id == PasswordResetToken.user_id)
        .where(User.email == "rerr@test.com")
    )
    tokens = result.scalars().all()
    assert len(tokens) == 1
    assert tokens[0].used_at is None


# ─────────────────────── reset_password ─────────────────────────────────

async def test_reset_password_full_cascade(db_session, mock_resend):
    """Proves three invariants in one test:
       (a) password hash updated
       (b) all sessions for user deleted
       (c) reset token marked used
    """
    old_token, user_id = await auth_service.signup(
        "reset@test.com", "oldpassword", db_session
    )
    await auth_service.forgot_password("reset@test.com", db_session)

    # Fetch the fresh reset token
    result = await db_session.execute(
        select(PasswordResetToken.token)
        .where(PasswordResetToken.user_id == user_id)
        .where(PasswordResetToken.used_at.is_(None))
    )
    reset_token_str = result.scalar_one()

    # Capture old hash
    user_result = await db_session.execute(
        select(User).where(User.id == user_id)
    )
    old_hash = user_result.scalar_one().password_hash

    await auth_service.reset_password(
        reset_token_str, "newpassword", db_session
    )

    # (a) password hash changed — re-query fresh; Result from line 243
    # is single-use and already consumed by the old_hash capture above.
    new_result = await db_session.execute(
        select(User.password_hash).where(User.id == user_id)
    )
    new_hash = new_result.scalar_one()
    assert new_hash != old_hash
    assert new_hash.startswith("$2b$")

    # (b) all sessions deleted (old signup session should be gone)
    sessions = await db_session.execute(
        select(Session).where(Session.user_id == user_id)
    )
    assert sessions.scalar_one_or_none() is None

    # (c) reset token marked used
    token_result = await db_session.execute(
        select(PasswordResetToken)
        .where(PasswordResetToken.token == reset_token_str)
    )
    token_row = token_result.scalar_one()
    assert token_row.used_at is not None

    # Sanity: old password no longer works, new password does
    with pytest.raises(HTTPException):
        await auth_service.login("reset@test.com", "oldpassword", db_session)
    new_login = await auth_service.login(
        "reset@test.com", "newpassword", db_session
    )
    assert len(new_login["token"]) > 100


@pytest.mark.parametrize(
    "scenario",
    ["invalid", "used", "expired"],
)
async def test_reset_password_rejects_bad_token(
    db_session, mock_resend, scenario
):
    """Three rejection paths, each → 400 with distinct error code."""
    if scenario == "invalid":
        bad_token = str(uuid4())  # never inserted
        expected_error = "token_invalid"
    elif scenario == "used":
        await auth_service.signup("used@test.com", "password123", db_session)
        await auth_service.forgot_password("used@test.com", db_session)
        result = await db_session.execute(
            select(PasswordResetToken.token)
            .join(User, User.id == PasswordResetToken.user_id)
            .where(User.email == "used@test.com")
        )
        bad_token = result.scalar_one()
        await auth_service.reset_password(
            bad_token, "newpassword", db_session
        )  # first use succeeds
        expected_error = "token_used"
    else:  # expired
        await auth_service.signup("exp@test.com", "password123", db_session)
        await auth_service.forgot_password("exp@test.com", db_session)
        result = await db_session.execute(
            select(PasswordResetToken)
            .join(User, User.id == PasswordResetToken.user_id)
            .where(User.email == "exp@test.com")
        )
        token_row = result.scalar_one()
        bad_token = token_row.token
        # Age the token
        await db_session.execute(
            update(PasswordResetToken)
            .where(PasswordResetToken.token == bad_token)
            .values(expires_at=datetime.utcnow() - timedelta(hours=2))
        )
        await db_session.commit()
        expected_error = "token_expired"

    with pytest.raises(HTTPException) as exc_info:
        await auth_service.reset_password(
            bad_token, "password999", db_session
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == expected_error
