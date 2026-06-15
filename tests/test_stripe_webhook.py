"""Stripe webhook tests — Phase 5.

construct_event is mocked for signature verification only; the service reads
fields from the JSON payload, so tests pass the event as JSON bytes.
"""
import json
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.main import app
from app.models.user import AccountType, User
from app.services import stripe_webhook_service

PERIOD_END = 1_900_000_000  # far-future unix timestamp


def _user(**kw) -> User:
    d = dict(
        email=f"{uuid4()}@test.com",
        password_hash="x",
        account_type=AccountType.individual,
        stripe_customer_id=f"cus_{uuid4().hex[:12]}",
    )
    d.update(kw)
    return User(**d)


async def _persist(db, user):
    db.add(user)
    await db.commit()
    return user


def _payload(etype, customer, status="active", sub_id="sub_123",
             period_end=PERIOD_END):
    return json.dumps({
        "type": etype,
        "data": {
            "object": {
                "customer": customer,
                "status": status,
                "id": sub_id,
                "current_period_end": period_end,
            }
        },
    }).encode()


@pytest.fixture
def construct(monkeypatch):
    """Mock construct_event for signature verification only.
    Set state['fail']=True to simulate a bad signature (raises)."""
    state = {"fail": False}

    def fake_construct(payload, sig, secret):
        if state["fail"]:
            raise ValueError("bad signature")
        return None  # return unused; the service reads json.loads(payload)

    monkeypatch.setattr(
        stripe_webhook_service.stripe.Webhook, "construct_event", fake_construct
    )
    return state


async def test_subscription_created_trialing(db_session, construct):
    u = await _persist(db_session, _user())
    payload = _payload(
        "customer.subscription.created", u.stripe_customer_id, status="trialing"
    )
    await stripe_webhook_service.process_event(payload, "sig", db_session)
    assert u.subscription_status == "trialing"
    assert u.subscription_source == "stripe"
    assert u.stripe_subscription_id == "sub_123"
    assert u.subscription_expires_at is not None


@pytest.mark.parametrize(
    "st,expected",
    [("active", "active"), ("past_due", "billing_retry"), ("canceled", "expired")],
)
async def test_subscription_updated_status_map(db_session, construct, st, expected):
    u = await _persist(db_session, _user())
    payload = _payload(
        "customer.subscription.updated", u.stripe_customer_id, status=st
    )
    await stripe_webhook_service.process_event(payload, "sig", db_session)
    assert u.subscription_status == expected


async def test_subscription_deleted_expires(db_session, construct):
    u = await _persist(db_session, _user(subscription_status="active"))
    payload = _payload(
        "customer.subscription.deleted", u.stripe_customer_id, status="canceled"
    )
    await stripe_webhook_service.process_event(payload, "sig", db_session)
    assert u.subscription_status == "expired"


async def test_period_end_from_items_fallback(db_session, construct):
    """Newer API (dahlia): current_period_end is on items, not top level."""
    u = await _persist(db_session, _user())
    payload = json.dumps({
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "customer": u.stripe_customer_id,
                "status": "trialing",
                "id": "sub_999",
                "items": {"data": [{"current_period_end": PERIOD_END}]},
            }
        },
    }).encode()
    await stripe_webhook_service.process_event(payload, "sig", db_session)
    assert u.subscription_status == "trialing"
    assert u.subscription_expires_at is not None


async def test_period_end_from_trial_end(db_session, construct):
    """Trialing sub with no current_period_end: expires_at falls back to trial_end."""
    u = await _persist(db_session, _user())
    payload = json.dumps({
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "customer": u.stripe_customer_id,
                "status": "trialing",
                "id": "sub_trial",
                "trial_end": PERIOD_END,
            }
        },
    }).encode()
    await stripe_webhook_service.process_event(payload, "sig", db_session)
    assert u.subscription_status == "trialing"
    assert u.subscription_expires_at is not None


async def test_bad_signature_raises_valueerror(db_session, construct):
    construct["fail"] = True
    with pytest.raises(ValueError):
        await stripe_webhook_service.process_event(b"{}", "badsig", db_session)


async def test_unknown_customer_is_noop(db_session, construct):
    payload = _payload(
        "customer.subscription.updated", "cus_nonexistent", status="active"
    )
    await stripe_webhook_service.process_event(payload, "sig", db_session)


@pytest_asyncio.fixture(loop_scope="session")
async def client(db_session):
    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_webhook_endpoint_received(client, db_session, construct):
    u = await _persist(db_session, _user())
    payload = _payload(
        "customer.subscription.updated", u.stripe_customer_id, status="active"
    )
    r = await client.post(
        "/webhooks/stripe", content=payload, headers={"stripe-signature": "sig"}
    )
    assert r.status_code == 200
    assert r.json()["received"] is True
