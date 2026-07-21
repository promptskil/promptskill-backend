"""Auth router HTTP-boundary tests — Phase 3 — Step 3.3b.

Scope: locks the router layer contract (HTTP methods, status codes,
response shapes, dependency wiring). Service-layer behavior is already
covered in test_auth_service.py — these tests do NOT re-prove business
logic, they prove the ASGI boundary.

Strategy:
  - httpx.AsyncClient + ASGITransport hit the FastAPI app in-process
    (no uvicorn, no network).
  - get_db is overridden to yield the SAVEPOINT-bound db_session
    fixture. Router, auth dependency, and service all share ONE
    AsyncSession per test → all mutations roll back at teardown.
  - All autouse fixtures from conftest apply: _fast_bcrypt,
    mock_resend, _mock_dlq_sinks, _celery_eager.

Gate coverage (from /build-checklist Step 3.3b):
  1. Full signup → validate → logout cycle
  2. Full forgot → reset → login cycle
  3. 401 without token on protected endpoint (logout)
"""
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.cookies import SESSION_COOKIE_NAME
from app.database import get_db
from app.main import app
from app.models import (
    EmailVerificationToken,
    PasswordResetToken,
    Session,
    User,
)
from tests.auth_helpers import login_verified_user, signup_verify_login


# ─────────────────────── async client fixture ───────────────────────────

@pytest_asyncio.fixture(loop_scope="session")
async def client(db_session):
    """HTTP client with get_db override → shared SAVEPOINT session.

    Why override instead of spinning up a real DB per request: each
    FastAPI request would open a fresh engine-pool connection and
    bypass the outer-transaction SAVEPOINT that keeps tests clean.
    Overriding get_db to hand out the one db_session fixture session
    means every handler in a test reuses the same savepoint-bound
    session — mutations persist across requests within a test and
    roll back at teardown.
    """
    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ─────────────────────── Gate 1: signup → validate → logout ─────────────


async def _verification_token_for(db_session, email: str) -> EmailVerificationToken:
    result = await db_session.execute(
        select(EmailVerificationToken)
        .join(User, User.id == EmailVerificationToken.user_id)
        .where(User.email == email)
    )
    return result.scalar_one()


async def test_signup_validate_logout_cycle(client, db_session):
    """Full happy-path lifecycle through the HTTP boundary."""
    # 1. signup
    r = await client.post(
        "/auth/signup",
        json={"email": "router-cycle@test.com", "password": "password123"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["email_verification_required"] is True
    assert "token" not in body
    assert "user_id" in body
    user_id = body["user_id"]
    UUID(user_id)  # shape check
    token = await login_verified_user(
        client, db_session, "router-cycle@test.com", user_id
    )

    # 2. validate - returns 200 with {valid:True, user_id}
    r = await client.post("/auth/validate", json={"token": token})
    assert r.status_code == 200
    assert r.json() == {
        "valid": True,
        "user_id": user_id,
        "checkout_required": True,
    }

    # 3. logout — 200 with Authorization bearer
    r = await client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json() == {"success": True}

    # 4. validate after logout — session deleted → invalid (not expired)
    r = await client.post("/auth/validate", json={"token": token})
    assert r.status_code == 200
    assert r.json() == {"valid": False, "reason": "invalid"}


async def test_signup_duplicate_email_returns_409(client):
    """Router surfaces auth_service HTTPException 409 unchanged."""
    payload = {"email": "router-dup@test.com", "password": "password123"}
    r1 = await client.post("/auth/signup", json=payload)
    assert r1.status_code == 200
    r2 = await client.post("/auth/signup", json=payload)
    assert r2.status_code == 409
    assert r2.json()["detail"]["error"] == "email_exists"


async def test_verify_email_code_marks_user_verified(
    client, db_session
):
    email = "router-verify@test.com"
    r = await client.post(
        "/auth/signup",
        json={"email": email, "password": "password123"},
    )
    assert r.status_code == 200

    verification_token = await _verification_token_for(db_session, email)

    r = await client.post(
        "/auth/verify-email-code",
        json={"email": email, "code": verification_token.token},
    )
    assert r.status_code == 200
    assert r.json()["success"] is True

    await db_session.refresh(verification_token)
    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    assert user.email_verified_at is not None
    assert verification_token.used_at is not None


async def test_verify_email_code_invalid_returns_400(client, db_session):
    email = "router-badcode@test.com"
    await client.post(
        "/auth/signup",
        json={"email": email, "password": "password123"},
    )
    vt = await _verification_token_for(db_session, email)
    wrong = "000000" if vt.token != "000000" else "111111"
    r = await client.post(
        "/auth/verify-email-code",
        json={"email": email, "code": wrong},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "code_invalid"


async def test_verify_email_code_expired_returns_400(client, db_session):
    email = "router-expired@test.com"
    r = await client.post(
        "/auth/signup",
        json={"email": email, "password": "password123"},
    )
    assert r.status_code == 200

    verification_token = await _verification_token_for(db_session, email)
    verification_token.expires_at = datetime.utcnow() - timedelta(minutes=1)
    await db_session.commit()

    r = await client.post(
        "/auth/verify-email-code",
        json={"email": email, "code": verification_token.token},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "code_expired"


async def test_verify_email_code_limiter_enabled_returns_200(
    client, db_session, rate_limit_enabled
):
    """Regression: slowapi injects rate-limit headers into `response`, so
    the endpoint must declare `response: Response` or it 500s in prod. CI
    runs with the limiter disabled, so this path is otherwise untested.
    """
    email = "router-limiter@test.com"
    await client.post(
        "/auth/signup",
        json={"email": email, "password": "password123"},
    )
    vt = await _verification_token_for(db_session, email)
    r = await client.post(
        "/auth/verify-email-code",
        json={"email": email, "code": vt.token},
    )
    assert r.status_code == 200
    assert r.json()["success"] is True


async def test_login_invalid_credentials_returns_401(client):
    """Email-enumeration guard — uniform 401 regardless of cause."""
    r = await client.post(
        "/auth/login",
        json={"email": "ghost@test.com", "password": "password123"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "invalid_credentials"


# ─────────────────────── Gate 2: forgot → reset → login ─────────────────

async def test_forgot_reset_login_cycle(client, db_session):
    """Password-reset lifecycle through the HTTP boundary.

    Seeds a user via /auth/signup (real Resend is mocked autouse),
    triggers /auth/forgot-password, pulls the fresh reset token out
    of the DB (the router returns only {success}, not the token —
    in prod the token ships via email), resets the password, then
    proves old pw rejects and new pw accepts.
    """
    # 1. seed user
    r = await client.post(
        "/auth/signup",
        json={"email": "router-reset@test.com", "password": "oldpassword"},
    )
    assert r.status_code == 200

    # 2. forgot-password → 200 {success}
    r = await client.post(
        "/auth/forgot-password",
        json={"email": "router-reset@test.com"},
    )
    assert r.status_code == 200
    assert r.json() == {"success": True}

    # 3. pull the fresh reset token from DB (prod ships via email)
    result = await db_session.execute(
        select(PasswordResetToken.token)
        .join(User, User.id == PasswordResetToken.user_id)
        .where(User.email == "router-reset@test.com")
        .where(PasswordResetToken.used_at.is_(None))
    )
    reset_token = result.scalar_one()

    # 4. reset-password → 200
    r = await client.post(
        "/auth/reset-password",
        json={"token": reset_token, "password": "newpassword"},
    )
    assert r.status_code == 200
    assert r.json() == {"success": True}

    # 5. old pw → 401
    r = await client.post(
        "/auth/login",
        json={"email": "router-reset@test.com", "password": "oldpassword"},
    )
    assert r.status_code == 401

    # 6. new pw → 200 with fresh token
    r = await client.post(
        "/auth/login",
        json={"email": "router-reset@test.com", "password": "newpassword"},
    )
    assert r.status_code == 200
    assert len(r.json()["token"]) > 100


async def test_forgot_password_missing_user_returns_404(client):
    """Spec line 1212 known email-enumeration gap surfaces at router."""
    r = await client.post(
        "/auth/forgot-password",
        json={"email": "never-signed-up@test.com"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "user_not_found"


async def test_reset_password_invalid_token_returns_400(client):
    """Router surfaces auth_service 400 with error code for bad token."""
    r = await client.post(
        "/auth/reset-password",
        json={
            "token": "00000000-0000-0000-0000-000000000000",
            "password": "newpassword",
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "token_invalid"


# ─────────────────────── Gate 3: 401 without token ──────────────────────

async def test_logout_without_authorization_returns_401(client):
    """Protected endpoint — missing Authorization header → 401.

    Covers the router-level bearer_scheme guard (auto_error=False
    means FastAPI returns None, router raises HTTPException 401).
    """
    r = await client.post("/auth/logout")  # no Authorization header
    assert r.status_code == 401


# ─────────────────────── validation surface ─────────────────────────────

async def test_signup_short_password_returns_400(client):
    """Pydantic min_length=8 → RequestValidationError → 400 (not 422).

    Proves the app-level 422→400 exception_handler is wired for the
    auth router (see app/exceptions.py + app/main.py).
    """
    r = await client.post(
        "/auth/signup",
        json={"email": "short-pw@test.com", "password": "short"},
    )
    assert r.status_code == 400


# ─────────────────────── web-origin session lifetime ────────────────────

async def test_login_web_origin_gets_short_session(client, db_session):
    """A login carrying an Origin header (browser/extension) → ~12h session,
    not the 30-day native default. S2 interim mitigation."""
    email = "router-web-ttl@test.com"
    await client.post(
        "/auth/signup", json={"email": email, "password": "password123"}
    )
    vt = await _verification_token_for(db_session, email)
    await client.post(
        "/auth/verify-email-code", json={"email": email, "code": vt.token}
    )

    r = await client.post(
        "/auth/login",
        json={"email": email, "password": "password123"},
        headers={"Origin": "https://vaineai.com"},
    )
    assert r.status_code == 200, r.text

    result = await db_session.execute(
        select(Session)
        .join(User, User.id == Session.user_id)
        .where(User.email == email)
    )
    session = result.scalar_one()
    delta = session.expires_at - datetime.utcnow()
    assert timedelta(hours=11) < delta < timedelta(hours=13)


# ─────────────────────── S2 3.3 — login sets web cookie ─────────────────

async def _signup_and_verify(client, db_session, email: str) -> None:
    await client.post(
        "/auth/signup", json={"email": email, "password": "password123"}
    )
    vt = await _verification_token_for(db_session, email)
    await client.post(
        "/auth/verify-email-code", json={"email": email, "code": vt.token}
    )


async def test_login_web_origin_sets_cookie(client, db_session):
    email = f"cookie-web-{uuid4().hex[:8]}@test.com"
    await _signup_and_verify(client, db_session, email)
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": "password123"},
        headers={"Origin": "https://www.vaineai.com"},
    )
    assert r.status_code == 200, r.text
    sc = r.headers.get("set-cookie", "")
    assert f"{SESSION_COOKIE_NAME}=" in sc
    assert "httponly" in sc.lower()
    assert "secure" in sc.lower()
    assert "samesite=lax" in sc.lower()
    assert r.json()["token"]  # body token still returned (mobile parity)


async def test_login_no_origin_sets_no_cookie(client, db_session):
    email = f"cookie-mobile-{uuid4().hex[:8]}@test.com"
    await _signup_and_verify(client, db_session, email)
    r = await client.post(
        "/auth/login", json={"email": email, "password": "password123"}
    )
    assert r.status_code == 200, r.text
    assert SESSION_COOKIE_NAME not in r.headers.get("set-cookie", "")


async def test_login_extension_origin_sets_no_cookie(client, db_session):
    email = f"cookie-ext-{uuid4().hex[:8]}@test.com"
    await _signup_and_verify(client, db_session, email)
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": "password123"},
        headers={"Origin": "chrome-extension://kgjcnldjmhbploedmijadigchnociecg"},
    )
    assert r.status_code == 200, r.text
    assert SESSION_COOKIE_NAME not in r.headers.get("set-cookie", "")


# ─────────────────────── S2 3.4 — logout clears cookie ──────────────────

async def test_logout_cookie_only_clears_and_deletes(client, db_session):
    email = f"logout-cookie-{uuid4().hex[:8]}@test.com"
    await _signup_and_verify(client, db_session, email)
    token = (
        await client.post(
            "/auth/login", json={"email": email, "password": "password123"}
        )
    ).json()["token"]

    r = await client.post("/auth/logout", cookies={SESSION_COOKIE_NAME: token})
    assert r.status_code == 200, r.text
    sc = r.headers.get("set-cookie", "")
    assert f"{SESSION_COOKIE_NAME}=" in sc
    assert "max-age=0" in sc.lower()  # cookie cleared

    rows = await db_session.execute(
        select(Session).join(User, User.id == Session.user_id)
        .where(User.email == email)
    )
    assert rows.scalars().all() == []  # session row deleted


async def test_logout_deletes_both_bearer_and_cookie_sessions(client, db_session):
    email = f"logout-both-{uuid4().hex[:8]}@test.com"
    await _signup_and_verify(client, db_session, email)
    # Two distinct sessions: mobile (30d) vs web (12h) → different exp → token.
    t_bearer = (
        await client.post(
            "/auth/login", json={"email": email, "password": "password123"}
        )
    ).json()["token"]
    t_cookie = (
        await client.post(
            "/auth/login",
            json={"email": email, "password": "password123"},
            headers={"Origin": "https://www.vaineai.com"},
        )
    ).json()["token"]
    assert t_bearer != t_cookie

    r = await client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {t_bearer}"},
        cookies={SESSION_COOKIE_NAME: t_cookie},
    )
    assert r.status_code == 200, r.text
    assert f"{SESSION_COOKIE_NAME}=" in r.headers.get("set-cookie", "")
    assert "max-age=0" in r.headers.get("set-cookie", "").lower()

    rows = await db_session.execute(
        select(Session).join(User, User.id == Session.user_id)
        .where(User.email == email)
    )
    assert rows.scalars().all() == []  # both rows gone (refinement #3)


# ─────────────────────── S2 3.8a — GET /auth/me probe ───────────────────

async def test_me_authed_returns_checkout_required(client, db_session):
    token, _ = await signup_verify_login(
        client, db_session, f"me-{uuid4().hex[:8]}@test.com"
    )
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()["checkout_required"] is True  # fresh user, no subscription


async def test_me_cookie_authed_200(client, db_session):
    token, _ = await signup_verify_login(
        client, db_session, f"me-cookie-{uuid4().hex[:8]}@test.com"
    )
    r = await client.get("/auth/me", cookies={SESSION_COOKIE_NAME: token})
    assert r.status_code == 200, r.text


async def test_me_bearer_precedence_over_cookie(client, db_session):
    """Bearer wins over a garbage cookie — matches get_current_user's contract."""
    token, _ = await signup_verify_login(
        client, db_session, f"me-both-{uuid4().hex[:8]}@test.com"
    )
    r = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        cookies={SESSION_COOKIE_NAME: "garbage-not-a-jwt"},
    )
    assert r.status_code == 200, r.text


async def test_me_no_auth_returns_401(client):
    r = await client.get("/auth/me")
    assert r.status_code == 401


# ─────────────────── email-endpoint rate limits (1/15 min) ───────────────

async def test_forgot_password_rate_limited(client, rate_limit_enabled):
    r1 = await client.post(
        "/auth/forgot-password", json={"email": "nobody@test.com"}
    )
    assert r1.status_code == 404  # unknown email (not 500 → response param wired)
    r2 = await client.post(
        "/auth/forgot-password", json={"email": "nobody@test.com"}
    )
    assert r2.status_code == 429


async def test_resend_verification_rate_limited(client, rate_limit_enabled):
    r1 = await client.post(
        "/auth/resend-verification", json={"email": "nobody@test.com"}
    )
    assert r1.status_code == 200  # always 200, no enumeration
    r2 = await client.post(
        "/auth/resend-verification", json={"email": "nobody@test.com"}
    )
    assert r2.status_code == 429
