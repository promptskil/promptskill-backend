"""Apple webhook tests — Part B.

JWS verification is mocked (no real Apple certs): `_verify_jws` is patched to
return canned payloads, so we test the dispatch + DB handlers + router, plus
the JWS structure/x5c error branches with synthetic tokens.
"""
import base64
import json
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.main import app
from app.models.user import User
from app.services import apple_webhook_service

PRO_PRODUCT = "com.airpromptskill.app.pro_biweekly"
EXPIRES_MS = 1_900_000_000_000  # milliseconds epoch (far future)


def _user(**kw) -> User:
    d = dict(
        email=f"{uuid4()}@test.com",
        password_hash="x",
    )
    d.update(kw)
    return User(**d)


async def _persist(db, user):
    db.add(user)
    await db.commit()
    return user


def _outer(ntype, subtype=""):
    return {
        "notificationType": ntype,
        "subtype": subtype,
        "data": {"signedTransactionInfo": "TX"},
    }


def _tx(otid, product=PRO_PRODUCT, expires_ms=EXPIRES_MS):
    return {
        "originalTransactionId": otid,
        "productId": product,
        "expiresDate": expires_ms,
    }


@pytest.fixture
def verify(monkeypatch):
    """Patch _verify_jws: returns state['tx'] for the nested 'TX' token,
    else state['outer']."""
    state = {"outer": None, "tx": None}

    def fake_verify(token):
        return state["tx"] if token == "TX" else state["outer"]

    monkeypatch.setattr(apple_webhook_service, "_verify_jws", fake_verify)
    return state


# ── dispatch ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "ntype,expected",
    [
        ("SUBSCRIBED", "active"),
        ("DID_RENEW", "active"),
        ("EXPIRED", "expired"),
        ("DID_FAIL_TO_RENEW", "billing_retry"),
        ("GRACE_PERIOD_EXPIRED", "expired"),
        ("REFUND", "expired"),
        ("REVOKE", "expired"),
    ],
)
async def test_dispatch_status(db_session, verify, ntype, expected):
    otid = f"otid_{uuid4().hex[:8]}"
    u = await _persist(db_session, _user(apple_original_transaction_id=otid))
    verify["outer"] = _outer(ntype)
    verify["tx"] = _tx(otid)
    await apple_webhook_service.process_notification("OUTER", db_session)
    assert u.subscription_status == expected


async def test_subscribed_sets_tier_and_expiry(db_session, verify):
    otid = f"otid_{uuid4().hex[:8]}"
    u = await _persist(db_session, _user(apple_original_transaction_id=otid))
    verify["outer"] = _outer("SUBSCRIBED")
    verify["tx"] = _tx(otid)
    await apple_webhook_service.process_notification("OUTER", db_session)
    assert u.subscription_tier == "pro"
    assert u.subscription_expires_at is not None


async def test_appaccounttoken_links_user(db_session, verify):
    """First notification: resolve by appAccountToken (user id) and link it."""
    u = await _persist(db_session, _user())  # no apple_original_transaction_id
    verify["outer"] = _outer("SUBSCRIBED")
    verify["tx"] = {
        "originalTransactionId": "otid_new",
        "appAccountToken": str(u.id),
        "productId": PRO_PRODUCT,
        "expiresDate": EXPIRES_MS,
    }
    await apple_webhook_service.process_notification("OUTER", db_session)
    assert u.subscription_status == "active"
    assert u.subscription_source == "apple"
    assert u.apple_original_transaction_id == "otid_new"  # link established


async def test_unknown_type_no_change(db_session, verify):
    otid = f"otid_{uuid4().hex[:8]}"
    u = await _persist(
        db_session,
        _user(apple_original_transaction_id=otid, subscription_status="active"),
    )
    verify["outer"] = _outer("CONSUMPTION_REQUEST")  # unhandled type
    verify["tx"] = _tx(otid)
    await apple_webhook_service.process_notification("OUTER", db_session)
    assert u.subscription_status == "active"  # untouched


async def test_missing_signed_tx_is_noop(db_session, verify):
    verify["outer"] = {
        "notificationType": "SUBSCRIBED",
        "subtype": "",
        "data": {},  # no signedTransactionInfo
    }
    await apple_webhook_service.process_notification("OUTER", db_session)


async def test_missing_otid_is_noop(db_session, verify):
    verify["outer"] = _outer("SUBSCRIBED")
    verify["tx"] = {"productId": PRO_PRODUCT, "expiresDate": EXPIRES_MS}
    await apple_webhook_service.process_notification("OUTER", db_session)


async def test_user_not_found_is_noop(db_session, verify):
    verify["outer"] = _outer("SUBSCRIBED")
    verify["tx"] = _tx("otid_nonexistent")
    await apple_webhook_service.process_notification("OUTER", db_session)


# ── JWS helper error branches (real code, synthetic tokens) ────────────────

def _b64url(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def test_decode_jws_unverified_bad_structure():
    with pytest.raises(ValueError):
        apple_webhook_service._decode_jws_unverified("only.two")


def test_verify_jws_bad_structure():
    with pytest.raises(ValueError):
        apple_webhook_service._verify_jws("only.two")


def test_verify_jws_short_x5c_chain():
    token = f"{_b64url({'x5c': []})}.{_b64url({})}.sig"
    with pytest.raises(ValueError):
        apple_webhook_service._verify_jws(token)


# ── router ─────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(loop_scope="session")
async def client(db_session):
    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_router_200(client, monkeypatch):
    async def ok(payload, db):
        return None

    monkeypatch.setattr("app.routers.webhooks.process_notification", ok)
    r = await client.post("/webhooks/apple", json={"signedPayload": "x"})
    assert r.status_code == 200
    assert r.json() == {"received": True}


async def test_router_400_on_valueerror(client, monkeypatch):
    async def bad(payload, db):
        raise ValueError("bad payload")

    monkeypatch.setattr("app.routers.webhooks.process_notification", bad)
    r = await client.post("/webhooks/apple", json={"signedPayload": "x"})
    assert r.status_code == 400


async def test_router_500_on_exception(client, monkeypatch):
    async def boom(payload, db):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.routers.webhooks.process_notification", boom)
    r = await client.post("/webhooks/apple", json={"signedPayload": "x"})
    assert r.status_code == 500
