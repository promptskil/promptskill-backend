"""Auth service regression tests.

Scope: locks Phase 3 service-layer behavior so Phase 4 Celery migration
has a pass/fail criterion. All tests run against real Postgres via
SAVEPOINT rollback (see conftest.py).

Test inventory (22 functions, D1 minimal + 1 parametrized reset rejection):
  1.  test_signup_happy_path
  2.  test_signup_duplicate_email_409
  3.  test_login_happy_path
  4.  test_login_requires_email_verification
  5.  test_login_invalid_credentials (parametrized: missing user, wrong pw)
  6.  test_validate_valid_token
  7.  test_validate_expired_session
  8.  test_validate_invalid_token
  9.  test_logout_deletes_session
  10. test_forgot_password_happy_invalidates_prior
  11. test_forgot_password_missing_user_404
  12. test_forgot_password_dispatch_failure_does_not_propagate
  13. test_reset_password_full_cascade
  14. test_reset_password_rejects_bad_token (parametrized: invalid, used, expired)
  15. test_resend_verification_unverified_issues_new_token
  16. test_resend_verification_already_verified_no_send
  17. test_resend_verification_unknown_email_no_send
  18. test_verify_email_code_happy
  19. test_verify_email_code_unknown_email
  20. test_verify_email_code_wrong_increments_attempts
  21. test_verify_email_code_lockout_after_max_attempts
  22. test_verify_email_code_expired
"""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
import resend
from fastapi import HTTPException
from sqlalchemy import select, update

from app.models import EmailVerificationToken, PasswordResetToken, Session, User
from app.services import auth_service


async def _verification_token_for(db_session, email: str) -> str:
    result = await db_session.execute(
        select(EmailVerificationToken.token)
        .join(User, User.id == EmailVerificationToken.user_id)
        .where(User.email == email)
        .where(EmailVerificationToken.used_at.is_(None))
    )
    return result.scalar_one()


async def _signup_and_verify(
    db_session,
    email: str,
    password: str = "password123",
):
    user_id = await auth_service.signup(email, password, db_session)
    token = await _verification_token_for(db_session, email)
    await auth_service.verify_email_code(email, token, db_session)
    return user_id


# ─────────────────────── signup ─────────────────────────────────────────

async def test_signup_happy_path(db_session, mock_resend):
    user_id = await auth_service.signup(
        "signup-happy@test.com", "password123", db_session
    )

    # User row persisted
    result = await db_session.execute(
        select(User).where(User.email == "signup-happy@test.com")
    )
    user = result.scalar_one()
    assert user.id == user_id
    assert user.password_hash.startswith("$2b$")  # bcrypt marker
    assert user.email_verified_at is None

    # Signup does not issue a session until the email is verified.
    result = await db_session.execute(
        select(Session).where(Session.user_id == user_id)
    )
    assert result.scalar_one_or_none() is None

    token_result = await db_session.execute(
        select(EmailVerificationToken)
        .where(EmailVerificationToken.user_id == user_id)
    )
    verification_token = token_result.scalar_one()
    assert verification_token.used_at is None
    assert verification_token.expires_at > datetime.utcnow()
    assert mock_resend.call_count == 1


async def test_signup_duplicate_email_409(db_session):
    await auth_service.signup("dup@test.com", "password123", db_session)
    with pytest.raises(HTTPException) as exc_info:
        await auth_service.signup("dup@test.com", "password123", db_session)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "email_exists"


# ─────────────────────── login ──────────────────────────────────────────

async def test_login_happy_path(db_session):
    signup_user_id = await _signup_and_verify(
        db_session, "login@test.com", "password123"
    )
    result = await auth_service.login(
        "login@test.com", "password123", db_session
    )
    assert result["user_id"] == signup_user_id
    assert len(result["token"]) > 100
    assert result["account_type"] == "individual"
    assert result["business_id"] is None

    # Signup does not issue a session; login creates the first one.
    result = await db_session.execute(
        select(Session).where(Session.user_id == signup_user_id)
    )
    assert len(result.scalars().all()) == 1


async def test_login_requires_email_verification(db_session):
    user_id = await auth_service.signup(
        "unverified@test.com", "password123", db_session
    )
    with pytest.raises(HTTPException) as exc_info:
        await auth_service.login(
            "unverified@test.com", "password123", db_session
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"] == "email_not_verified"

    result = await db_session.execute(
        select(Session).where(Session.user_id == user_id)
    )
    assert result.scalar_one_or_none() is None


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
    user_id = await _signup_and_verify(
        db_session, "val@test.com", "password123"
    )
    login = await auth_service.login("val@test.com", "password123", db_session)
    token = login["token"]
    result = await auth_service.validate_token(token, db_session)
    assert result == {"valid": True, "user_id": str(user_id)}


async def test_validate_expired_session(db_session):
    await _signup_and_verify(
        db_session, "exp@test.com", "password123"
    )
    login = await auth_service.login("exp@test.com", "password123", db_session)
    token = login["token"]
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
    user_id = await _signup_and_verify(
        db_session, "out@test.com", "password123"
    )
    login = await auth_service.login("out@test.com", "password123", db_session)
    token = login["token"]
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
    # Reset mock after signup — signup now sends a verification email.
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
    await auth_service.signup("rerr@test.com", "password123", db_session)
    mock_resend.reset_mock()
    mock_resend.side_effect = resend.exceptions.ResendError(
        code=401,
        message="API key is invalid",
        suggested_action="Use a valid API key",
        error_type="authentication_error",
    )

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


# ─────────────────────── resend_verification ────────────────────────

async def test_resend_verification_unverified_issues_new_token(
    db_session, mock_resend
):
    """Rule 1 (unverified re-entry): a fresh token is issued, the prior
    unused token is invalidated, and exactly one email is dispatched."""
    await auth_service.signup("resend@test.com", "password123", db_session)
    # signup already created token A + sent one email — isolate the resend.
    mock_resend.reset_mock()

    await auth_service.resend_verification("resend@test.com", db_session)

    result = await db_session.execute(
        select(EmailVerificationToken)
        .join(User, User.id == EmailVerificationToken.user_id)
        .where(User.email == "resend@test.com")
        .order_by(EmailVerificationToken.expires_at)
    )
    tokens = result.scalars().all()
    assert len(tokens) == 2
    assert tokens[0].used_at is not None, "prior token should be invalidated"
    assert tokens[1].used_at is None, "new token should be active"
    assert mock_resend.call_count == 1


async def test_resend_verification_already_verified_no_send(
    db_session, mock_resend
):
    """No-op with no signal: a verified account triggers no email and no
    new token (prevents abuse + verification-state leak)."""
    await _signup_and_verify(db_session, "verified-resend@test.com")
    mock_resend.reset_mock()

    await auth_service.resend_verification(
        "verified-resend@test.com", db_session
    )

    assert mock_resend.call_count == 0


async def test_resend_verification_unknown_email_no_send(
    db_session, mock_resend
):
    """Unknown email: silent no-op (no enumeration), no exception raised."""
    await auth_service.resend_verification("ghost-resend@test.com", db_session)
    assert mock_resend.call_count == 0


# ─────────────────────── verify_email_code ──────────────────────────────

async def test_verify_email_code_happy(db_session):
    await auth_service.signup("vcode@test.com", "password123", db_session)
    code = await _verification_token_for(db_session, "vcode@test.com")
    await auth_service.verify_email_code("vcode@test.com", code, db_session)
    result = await db_session.execute(
        select(User).where(User.email == "vcode@test.com")
    )
    assert result.scalar_one().email_verified_at is not None


async def test_verify_email_code_unknown_email(db_session):
    with pytest.raises(HTTPException) as exc:
        await auth_service.verify_email_code("ghost@test.com", "123456", db_session)
    assert exc.value.status_code == 400
    assert exc.value.detail["error"] == "code_invalid"


async def test_verify_email_code_wrong_increments_attempts(db_session):
    await auth_service.signup("vwrong@test.com", "password123", db_session)
    real = await _verification_token_for(db_session, "vwrong@test.com")
    wrong = "111111" if real != "111111" else "222222"
    with pytest.raises(HTTPException) as exc:
        await auth_service.verify_email_code("vwrong@test.com", wrong, db_session)
    assert exc.value.detail["error"] == "code_invalid"
    row = await db_session.execute(
        select(EmailVerificationToken)
        .join(User, User.id == EmailVerificationToken.user_id)
        .where(User.email == "vwrong@test.com")
    )
    assert row.scalar_one().attempts == 1


async def test_verify_email_code_lockout_after_max_attempts(db_session):
    await auth_service.signup("vlock@test.com", "password123", db_session)
    real = await _verification_token_for(db_session, "vlock@test.com")
    wrong = "111111" if real != "111111" else "222222"
    for _ in range(auth_service.EMAIL_VERIFICATION_MAX_ATTEMPTS):
        with pytest.raises(HTTPException) as exc:
            await auth_service.verify_email_code("vlock@test.com", wrong, db_session)
        assert exc.value.detail["error"] == "code_invalid"
    with pytest.raises(HTTPException) as exc:
        await auth_service.verify_email_code("vlock@test.com", wrong, db_session)
    assert exc.value.detail["error"] == "too_many_attempts"


async def test_verify_email_code_expired(db_session):
    await auth_service.signup("vexp@test.com", "password123", db_session)
    code = await _verification_token_for(db_session, "vexp@test.com")
    await db_session.execute(
        update(EmailVerificationToken)
        .where(EmailVerificationToken.token == code)
        .values(expires_at=datetime.utcnow() - timedelta(minutes=1))
    )
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await auth_service.verify_email_code("vexp@test.com", code, db_session)
    assert exc.value.detail["error"] == "code_expired"


# ─────────────────────── reset_password ─────────────────────────────────

async def test_reset_password_full_cascade(db_session, mock_resend):
    """Proves three invariants in one test:
       (a) password hash updated
       (b) all sessions for user deleted
       (c) reset token marked used
    """
    user_id = await auth_service.signup(
        "reset@test.com", "oldpassword", db_session
    )
    await auth_service.issue_session(user_id, db_session)
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

    verified_result = await db_session.execute(
        select(User.email_verified_at).where(User.id == user_id)
    )
    assert verified_result.scalar_one() is not None

    # (b) all sessions deleted
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
