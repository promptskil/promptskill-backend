"""Billing checkout tests — Phase 4. Stripe SDK fully mocked (no live calls)."""
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.main import app
from app.models.user import AccountType, User
from app.services import auth_service, billing_service


def _user(**kw) -> User:
    d = dict(
        email=f"{uuid4()}@test.com",
        password_hash="x",
        account_type=AccountType.individual,
    )
    d.update(kw)
    return User(**d)


async def _persist(db, user):
    db.add(user)
    await db.commit()
    return user


@pytest.fixture
def stripe_mocks(monkeypatch):
    state = {"customer_calls": 0, "customer_kwargs": None, "session_kwargs": None}

    def fake_customer_create(**kwargs):
        state["customer_calls"] += 1
        state["customer_kwargs"] = kwargs
        return {"id": "cus_test123"}

    def fake_session_create(**kwargs):
        state["session_kwargs"] = kwargs
        return {"url": "https://checkout.stripe.com/c/test"}

    monkeypatch.setattr(
        billing_service.stripe.Customer, "create", fake_customer_create
    )
    monkeypatch.setattr(
        billing_service.stripe.checkout.Session, "create", fake_session_create
    )
    return state


@pytest_asyncio.fixture(loop_scope="session")
async def client(db_session):
    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_new_user_creates_customer_and_session(db_session, stripe_mocks):
    u = await _persist(db_session, _user())
    url = await billing_service.create_checkout_session(u.id, db_session)

    assert url == "https://checkout.stripe.com/c/test"
    assert stripe_mocks["customer_calls"] == 1
    assert stripe_mocks["customer_kwargs"]["metadata"]["user_id"] == str(u.id)
    # Identity link persisted — the fundamental the webhook relies on.
    assert u.stripe_customer_id == "cus_test123"

    sk = stripe_mocks["session_kwargs"]
    assert sk["mode"] == "subscription"
    assert sk["payment_method_collection"] == "always"
    assert sk["subscription_data"]["trial_period_days"] == 7
    assert sk["customer"] == "cus_test123"


async def test_existing_customer_is_reused(db_session, stripe_mocks):
    u = await _persist(db_session, _user(stripe_customer_id="cus_existing"))
    await billing_service.create_checkout_session(u.id, db_session)

    assert stripe_mocks["customer_calls"] == 0  # not recreated
    assert stripe_mocks["session_kwargs"]["customer"] == "cus_existing"


async def test_user_not_found_raises(db_session, stripe_mocks):
    with pytest.raises(ValueError):
        await billing_service.create_checkout_session(uuid4(), db_session)


async def test_checkout_endpoint_returns_url(client, db_session, stripe_mocks):
    token, _ = await auth_service.signup(
        f"{uuid4()}@test.com", "password123", db_session
    )
    r = await client.post(
        "/billing/checkout", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    assert r.json()["url"] == "https://checkout.stripe.com/c/test"
