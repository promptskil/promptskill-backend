"""Apple webhook tests.

Signature/chain verification is delegated to Apple's official SignedDataVerifier
(covered by the library). Here we test dispatch + DB handlers + the ordering
(AP2) / expiry (AP3) / signedDate guards + the dual-environment fallback + the
router, by patching `_verify_notification` (and, for the fallback test,
`_verifiers`) to return canned decoded objects.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.user import User
from app.services import apple_webhook_service

PRO_PRODUCT = "com.airpromptskill.app.pro_biweekly"
EXPIRES_MS = 1_900_000_000_000  # far-future ms epoch
SIGNED_DATE_MS = 1_700_000_000_000


def _user(**kw) -> User:
    d = dict(email=f"{uuid4()}@test.com", password_hash="x")
    d.update(kw)
    return User(**d)


async def _persist(db, user):
    db.add(user)
    await db.commit()
    return user


def _outer(ntype, signed_date=SIGNED_DATE_MS):
    """A decoded outer notification (ResponseBodyV2DecodedPayload-shaped)."""
    return SimpleNamespace(
        rawNotificationType=ntype,
        subtype="",
        signedDate=signed_date,
        data=SimpleNamespace(signedTransactionInfo="TX"),
    )


def _tx(otid, product=PRO_PRODUCT, expires_ms=EXPIRES_MS, app_account_token=None):
    """A decoded transaction (JWSTransactionDecodedPayload-shaped)."""
    return SimpleNamespace(
        originalTransactionId=otid,
        productId=product,
        expiresDate=expires_ms,
        appAccountToken=app_account_token,
    )


@pytest.fixture
def verify(monkeypatch):
    """Patch _verify_notification → (stub_verifier, state['outer']); the stub's
    verify_and_decode_signed_transaction returns state['tx']."""
    state = {"outer": None, "tx": None}

    def fake_verify_notification(signed_payload):
        stub = SimpleNamespace(
            verify_and_decode_signed_transaction=lambda _tx: state["tx"]
        )
        return stub, state["outer"]

    monkeypatch.setattr(
        apple_webhook_service, "_verify_notification", fake_verify_notification
    )
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
    verify["tx"] = _tx("otid_new", app_account_token=str(u.id))
    await apple_webhook_service.process_notification("OUTER", db_session)
    assert u.subscription_status == "active"
    assert u.subscription_source == "apple"
    assert u.apple_original_transaction_id == "otid_new"


async def test_unknown_type_no_change(db_session, verify):
    otid = f"otid_{uuid4().hex[:8]}"
    u = await _persist(
        db_session,
        _user(apple_original_transaction_id=otid, subscription_status="active"),
    )
    verify["outer"] = _outer("CONSUMPTION_REQUEST")  # unhandled type
    verify["tx"] = _tx(otid)
    await apple_webhook_service.process_notification("OUTER", db_session)
    assert u.subscription_status == "active"


async def test_missing_signed_tx_is_noop(db_session, verify):
    verify["outer"] = SimpleNamespace(
        rawNotificationType="SUBSCRIBED", subtype="", signedDate=SIGNED_DATE_MS,
        data=SimpleNamespace(signedTransactionInfo=None),
    )
    await apple_webhook_service.process_notification("OUTER", db_session)


async def test_missing_otid_is_noop(db_session, verify):
    verify["outer"] = _outer("SUBSCRIBED")
    verify["tx"] = _tx("")  # empty originalTransactionId
    await apple_webhook_service.process_notification("OUTER", db_session)


async def test_user_not_found_is_noop(db_session, verify):
    verify["outer"] = _outer("SUBSCRIBED")
    verify["tx"] = _tx("otid_nonexistent")
    await apple_webhook_service.process_notification("OUTER", db_session)


# ── AP2 ordering guard (outer signedDate) ────────────────────────────────

async def test_stale_notification_does_not_apply(db_session, verify):
    otid = f"otid_{uuid4().hex[:8]}"
    u = await _persist(db_session, _user(apple_original_transaction_id=otid))
    verify["outer"] = _outer("EXPIRED", signed_date=2000)
    verify["tx"] = _tx(otid)
    await apple_webhook_service.process_notification("OUTER", db_session)
    assert u.subscription_status == "expired"
    # a delayed earlier SUBSCRIBED (signedDate < last) must be ignored
    verify["outer"] = _outer("SUBSCRIBED", signed_date=1000)
    verify["tx"] = _tx(otid)
    await apple_webhook_service.process_notification("OUTER", db_session)
    assert u.subscription_status == "expired"


# ── AP3 no null-expiry grants ────────────────────────────────────────────

async def test_grant_without_expires_is_skipped(db_session, verify):
    otid = f"otid_{uuid4().hex[:8]}"
    u = await _persist(
        db_session,
        _user(apple_original_transaction_id=otid, subscription_status="expired"),
    )
    verify["outer"] = _outer("SUBSCRIBED")
    verify["tx"] = _tx(otid, expires_ms=None)  # grant event without expiresDate
    await apple_webhook_service.process_notification("OUTER", db_session)
    assert u.subscription_status == "expired"        # not granted
    assert u.apple_last_signed_date is None           # recency not advanced


# ── signedDate guard ─────────────────────────────────────────────────────

async def test_missing_signed_date_raises(db_session, verify):
    otid = f"otid_{uuid4().hex[:8]}"
    await _persist(db_session, _user(apple_original_transaction_id=otid))
    verify["outer"] = _outer("SUBSCRIBED", signed_date=None)
    verify["tx"] = _tx(otid)
    with pytest.raises(ValueError):
        await apple_webhook_service.process_notification("OUTER", db_session)


# ── dual verifier: Production → Sandbox on INVALID_ENVIRONMENT ────────────

async def test_sandbox_fallback_on_invalid_environment(db_session, monkeypatch):
    from appstoreserverlibrary.signed_data_verifier import (
        VerificationException,
        VerificationStatus,
    )

    otid = f"otid_{uuid4().hex[:8]}"
    u = await _persist(db_session, _user(apple_original_transaction_id=otid))
    outer = _outer("SUBSCRIBED")
    tx = _tx(otid)

    def prod_verify(_):
        raise VerificationException(VerificationStatus.INVALID_ENVIRONMENT)

    prod = SimpleNamespace(verify_and_decode_notification=prod_verify)
    sandbox = SimpleNamespace(
        verify_and_decode_notification=lambda _: outer,
        verify_and_decode_signed_transaction=lambda _: tx,
    )
    monkeypatch.setattr(
        apple_webhook_service, "_verifiers", lambda: (prod, sandbox)
    )
    await apple_webhook_service.process_notification("OUTER", db_session)
    assert u.subscription_status == "active"


# ── verifier construction (real cert asset + config guard) ───────────────

def test_verifiers_build_with_valid_config(monkeypatch):
    """Exercises the committed Apple root .cer load + SignedDataVerifier build."""
    monkeypatch.setattr(settings, "APPLE_BUNDLE_ID", "com.airpromptskill.app")
    monkeypatch.setattr(settings, "APPLE_APP_APPLE_ID", 123456789)
    apple_webhook_service._verifiers.cache_clear()
    try:
        prod, sandbox = apple_webhook_service._verifiers()
        assert prod is not None and sandbox is not None
    finally:
        apple_webhook_service._verifiers.cache_clear()


def test_verifiers_missing_config_raises_runtimeerror(monkeypatch):
    """Server misconfig must be RuntimeError (→ 500), never ValueError (→ 400)."""
    monkeypatch.setattr(settings, "APPLE_BUNDLE_ID", "")
    apple_webhook_service._verifiers.cache_clear()
    try:
        with pytest.raises(RuntimeError):
            apple_webhook_service._verifiers()
    finally:
        apple_webhook_service._verifiers.cache_clear()


# ── router ────────────────────────────────────────────────────────────────

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
