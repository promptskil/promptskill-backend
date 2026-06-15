"""
Apple App Store Server Notifications v2 — webhook service.

Apple sends a signed JWT (JWS) in the body as `signedPayload`.
The payload contains notification type + a nested signed transaction JWS.

Verification flow:
  1. Decode JWS header → extract x5c certificate chain
  2. Verify chain: leaf ← intermediate ← Apple root CA
  3. Verify JWS signature with leaf cert public key (ES256)
  4. Parse payload, decode nested signedTransactionInfo JWS (same chain approach)
  5. Dispatch event to the correct handler

Environment variable required:
  APPLE_SHARED_SECRET — app-specific shared secret from App Store Connect
  (used for legacy receipt validation; chain verification is the primary guard here)

References:
  https://developer.apple.com/documentation/appstoreservernotifications
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ec import ECDSA
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

logger = logging.getLogger(__name__)

# ── Apple Root CA G3 — public certificate (PEM) ──────────────────────────────
# Source: https://www.apple.com/certificateauthority/
# This is the trusted anchor for all App Store signed payloads.
APPLE_ROOT_CA_G3_PEM = b"""-----BEGIN CERTIFICATE-----
MIICQzCCAcmgAwIBAgIILcX8iNLFS5UwCgYIKoZIzj0EAwMwZzEbMBkGA1UEAwwS
QXBwbGUgUm9vdCBDQSAtIEczMSYwJAYDVQQLDB1BcHBsZSBDZXJ0aWZpY2F0aW9u
IEF1dGhvcml0eTETMBEGA1UECgwKQXBwbGUgSW5jLjELMAkGA1UEBhMCVVMwHhcN
MTQwNDMwMTgxOTA2WhcNMzkwNDMwMTgxOTA2WjBnMRswGQYDVQQDDBJBcHBsZSBS
b290IENBIC0gRzMxJjAkBgNVBAsMHUFwcGxlIENlcnRpZmljYXRpb24gQXV0aG9y
aXR5MRMwEQYDVQQKDApBcHBsZSBJbmMuMQswCQYDVQQGEwJVUzB2MBAGByqGSM49
AgEGBSuBBAAiA2IABJjpLz1AcqTtkyJygnngoNEV6RfO4IKCvkemkT0TXMvr3AqX
VYoTbDRzqE7bPwHOFNDrFE/61o1cbI/CqHfNMY3JXiKQrMGnS9Q97kfgbSFf4O7G
5S8s3C2pUQMDGaMjMCEwHQYDVR0OBBYEFLuw3qFYM4iapIqZ3r6sWwISPnlRMA8G
A1UdEwEB/wQFMAMBAf8wCgYIKoZIzj0EAwMDaAAwZQIxAIOQn9QtqR0DFf7j9Us
zylC3JtlzPqJtCWnpSBeASPMOMFBPXcTokRQv3bSa8HbgIwDrLMnO9pNVNlQuggn
Pr4O1rl8B9M36VKb6rWfJtFjGTQA8f9MkRAdBigJef+N
-----END CERTIFICATE-----"""


# ── Notification types we care about ─────────────────────────────────────────
NOTIFICATION_SUBSCRIBED       = "SUBSCRIBED"
NOTIFICATION_DID_RENEW        = "DID_RENEW"
NOTIFICATION_EXPIRED          = "EXPIRED"
NOTIFICATION_DID_FAIL_TO_RENEW = "DID_FAIL_TO_RENEW"
NOTIFICATION_GRACE_PERIOD_EXPIRED = "GRACE_PERIOD_EXPIRED"
NOTIFICATION_REFUND           = "REFUND"
NOTIFICATION_PRICE_INCREASE   = "PRICE_INCREASE"
NOTIFICATION_REVOKE           = "REVOKE"


# ── Product ID → tier mapping  ────────────────────────────────────────────────
# Update these values to match your actual product IDs in App Store Connect.
PRODUCT_TIER_MAP: dict[str, str] = {
    "com.airpromptskill.app.starter_biweekly": "starter",
    "com.airpromptskill.app.pro_biweekly":     "pro",
}


# ── JWS helpers ───────────────────────────────────────────────────────────────

def _b64url_decode(segment: str) -> bytes:
    """Decode a base64url segment (no padding required)."""
    padding = 4 - len(segment) % 4
    if padding != 4:
        segment += "=" * padding
    return base64.urlsafe_b64decode(segment)


def _decode_jws_unverified(token: str) -> tuple[dict, dict]:
    """Return (header, payload) without signature verification."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWS: expected 3 parts")
    header  = json.loads(_b64url_decode(parts[0]))
    payload = json.loads(_b64url_decode(parts[1]))
    return header, payload


def _load_cert_from_b64(b64_der: str) -> x509.Certificate:
    """Load an X.509 certificate from a base64-encoded DER string."""
    der = base64.b64decode(b64_der)
    return x509.load_der_x509_certificate(der)


def _verify_jws(token: str) -> dict:
    """
    Verify an Apple-signed JWS and return the payload dict.

    Steps:
      1. Decode header → extract x5c chain
      2. Verify chain terminates at Apple Root CA G3
      3. Verify token signature with leaf cert public key
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWS structure")

    header_raw, payload_raw, sig_raw = parts
    header = json.loads(_b64url_decode(header_raw))

    x5c: list[str] = header.get("x5c", [])
    if len(x5c) < 2:
        raise ValueError(
            "JWS x5c chain too short — expected at least leaf + intermediate"
        )

    # Load certificate chain
    leaf_cert  = _load_cert_from_b64(x5c[0])
    inter_cert = _load_cert_from_b64(x5c[1])
    root_cert  = x509.load_pem_x509_certificate(APPLE_ROOT_CA_G3_PEM)

    # Verify intermediate was signed by root
    try:
        root_pub = root_cert.public_key()
        root_pub.verify(
            inter_cert.signature,
            inter_cert.tbs_certificate_bytes,
            ECDSA(hashes.SHA384()),
        )
    except InvalidSignature as exc:
        raise ValueError("Intermediate cert not signed by Apple Root CA") from exc

    # Verify leaf was signed by intermediate
    try:
        inter_pub = inter_cert.public_key()
        inter_pub.verify(
            leaf_cert.signature,
            leaf_cert.tbs_certificate_bytes,
            ECDSA(hashes.SHA256()),
        )
    except InvalidSignature as exc:
        raise ValueError("Leaf cert not signed by intermediate") from exc

    # Verify JWS signature using leaf public key
    signing_input = f"{header_raw}.{payload_raw}".encode()
    signature     = _b64url_decode(sig_raw)
    try:
        leaf_cert.public_key().verify(signature, signing_input, ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise ValueError("JWS signature verification failed") from exc

    return json.loads(_b64url_decode(payload_raw))


# ── Main entry point ──────────────────────────────────────────────────────────

async def process_notification(signed_payload: str, db: AsyncSession) -> None:
    """
    Parse, verify, and dispatch an Apple App Store Server Notification.
    Raises ValueError on invalid payload (caller returns 400).
    Logs and swallows DB errors so Apple retries on 500s.
    """
    try:
        payload = _verify_jws(signed_payload)
    except ValueError as exc:
        logger.warning("Apple webhook JWS verification failed: %s", exc)
        raise

    notification_type: str = payload.get("notificationType", "")
    subtype: str           = payload.get("subtype", "")
    data: dict             = payload.get("data", {})

    logger.info(
        "Apple notification received: type=%s subtype=%s",
        notification_type, subtype,
    )

    # Decode nested signedTransactionInfo
    signed_tx = data.get("signedTransactionInfo", "")
    if not signed_tx:
        logger.warning("Apple notification missing signedTransactionInfo — skipping")
        return

    try:
        tx = _verify_jws(signed_tx)
    except ValueError as exc:
        logger.warning("signedTransactionInfo verification failed: %s", exc)
        raise

    original_transaction_id: str = tx.get("originalTransactionId", "")
    app_account_token: str       = tx.get("appAccountToken", "")
    product_id: str              = tx.get("productId", "")
    expires_ms: int | None       = tx.get("expiresDate")  # milliseconds epoch
    tier = PRODUCT_TIER_MAP.get(product_id)

    expires_at: datetime | None = None
    if expires_ms:
        expires_at = datetime.fromtimestamp(expires_ms / 1000, tz=timezone.utc)

    if not original_transaction_id:
        logger.warning("Apple notification missing originalTransactionId — skipping")
        return

    user = await _resolve_user(db, original_transaction_id, app_account_token)
    if user is None:
        logger.warning(
            "Apple %s: no user (txId=%s appAccountToken=%s)",
            notification_type, original_transaction_id, app_account_token,
        )
        return

    # Record the link so later notifications resolve by originalTransactionId.
    if not user.apple_original_transaction_id:
        user.apple_original_transaction_id = original_transaction_id

    # Dispatch — apply state to the resolved user.
    try:
        if notification_type in (NOTIFICATION_SUBSCRIBED, NOTIFICATION_DID_RENEW):
            user.subscription_status = "active"
            user.subscription_expires_at = expires_at
            user.subscription_source = "apple"
            if tier:
                user.subscription_tier = tier

        elif notification_type == NOTIFICATION_DID_FAIL_TO_RENEW:
            # Still in grace period — keep access, flag billing_retry.
            user.subscription_status = "billing_retry"
            user.subscription_expires_at = expires_at

        elif notification_type in (
            NOTIFICATION_EXPIRED,
            NOTIFICATION_GRACE_PERIOD_EXPIRED,
            NOTIFICATION_REFUND,
            NOTIFICATION_REVOKE,
        ):
            user.subscription_status = "expired"
            user.subscription_expires_at = None

        else:
            logger.info(
                "Apple notification type %s — no action taken",
                notification_type,
            )

        await db.commit()
        logger.info(
            "Apple %s applied: user=%s status=%s",
            notification_type, user.id, user.subscription_status,
        )

    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.error(
            "Apple webhook DB error for txId=%s: %s",
            original_transaction_id,
            exc,
        )
        raise  # propagate → 500 → Apple retries


# ── User resolution ─────────────────────────────────────────────────────────

async def _resolve_user(
    db: AsyncSession, original_transaction_id: str, app_account_token: str
) -> User | None:
    """Resolve the user for an Apple notification.

    First by apple_original_transaction_id (already linked). If not found and
    appAccountToken is present — the user id the iOS app sets on the StoreKit
    purchase — resolve by user id, establishing the link on first notification.
    """
    result = await db.execute(
        select(User).where(
            User.apple_original_transaction_id == original_transaction_id
        )
    )
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    if app_account_token:
        try:
            user_id = UUID(app_account_token)
        except (ValueError, TypeError):
            logger.warning(
                "Apple appAccountToken not a valid UUID: %s", app_account_token
            )
            return None
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    return None
