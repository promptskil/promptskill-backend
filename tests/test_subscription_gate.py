"""Subscription gate tests — Phase 3.

Covers app/auth.py::_subscription_active (pure) and
require_active_subscription (db). The dependency is exercised directly
(it is not yet wired to an endpoint — that is Phase 6) by passing
user_id + db explicitly, bypassing the get_current_user Depends.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.auth import _subscription_active, require_active_subscription
from app.config import settings
from app.models.user import User

FUTURE = datetime.now(timezone.utc) + timedelta(days=5)
PAST = datetime.now(timezone.utc) - timedelta(days=1)


@pytest.fixture(autouse=True)
def _enable_paywall(monkeypatch):
    monkeypatch.setattr(settings, "PAYWALL_ENABLED", True)


def _user(**kw) -> User:
    defaults = dict(
        email=f"{uuid4()}@test.com",
        password_hash="x",
    )
    defaults.update(kw)
    return User(**defaults)


# ── _subscription_active (pure, no DB) ───────────────────────────────────

@pytest.mark.parametrize(
    "st", ["active", "trialing", "grace_period", "billing_retry"]
)
def test_active_statuses_with_future_expiry(st):
    user = _user(subscription_status=st, subscription_expires_at=FUTURE)
    assert _subscription_active(user) is True


@pytest.mark.parametrize("st", ["expired", None, "canceled"])
def test_inactive_statuses_denied(st):
    user = _user(subscription_status=st, subscription_expires_at=FUTURE)
    assert _subscription_active(user) is False


def test_active_but_past_expiry_denied():
    user = _user(subscription_status="active", subscription_expires_at=PAST)
    assert _subscription_active(user) is False


def test_active_with_null_expiry_trusts_status():
    user = _user(subscription_status="active", subscription_expires_at=None)
    assert _subscription_active(user) is True


# ── require_active_subscription (DB) ─────────────────────────────────────

async def _persist(db, user):
    db.add(user)
    await db.commit()
    return user


async def test_individual_active_allowed(db_session):
    u = await _persist(
        db_session,
        _user(subscription_status="active", subscription_expires_at=FUTURE),
    )
    assert await require_active_subscription(user_id=u.id, db=db_session) == u.id


async def test_individual_trialing_allowed(db_session):
    u = await _persist(
        db_session,
        _user(subscription_status="trialing", subscription_expires_at=FUTURE),
    )
    assert await require_active_subscription(user_id=u.id, db=db_session) == u.id


async def test_individual_expired_returns_402(db_session):
    u = await _persist(db_session, _user(subscription_status="expired"))
    with pytest.raises(HTTPException) as exc:
        await require_active_subscription(user_id=u.id, db=db_session)
    assert exc.value.status_code == 402


async def test_individual_no_subscription_returns_402(db_session):
    u = await _persist(db_session, _user())  # subscription_status NULL
    with pytest.raises(HTTPException) as exc:
        await require_active_subscription(user_id=u.id, db=db_session)
    assert exc.value.status_code == 402


async def test_user_not_found_returns_401(db_session):
    with pytest.raises(HTTPException) as exc:
        await require_active_subscription(user_id=uuid4(), db=db_session)
    assert exc.value.status_code == 401
