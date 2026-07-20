"""
Apple App Store Server Notifications v2 — webhook service.

Verification is delegated to Apple's official app-store-server-library
SignedDataVerifier, which performs the full check: certificate chain to the
Apple root, cert validity, Apple OIDs, environment + app-identity match, and
the ES256 signature. We run a dual verifier (Production, then Sandbox on
INVALID_ENVIRONMENT) so App Store sandbox test notifications still work while
Production stays strict.

The Apple Root CA - G3 is committed as a DER asset
(app/assets/AppleRootCA-G3.cer) obtained from Apple PKI — an auditable trust
anchor, not hand-copied text.

References:
  https://developer.apple.com/documentation/appstoreservernotifications
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from uuid import UUID

from appstoreserverlibrary.models.Environment import Environment
from appstoreserverlibrary.signed_data_verifier import (
    SignedDataVerifier,
    VerificationException,
    VerificationStatus,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)

_APPLE_ROOT_G3_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "AppleRootCA-G3.cer"
)

# ── Notification types ───────────────────────────────────────────────────────
NOTIFICATION_SUBSCRIBED = "SUBSCRIBED"
NOTIFICATION_DID_RENEW = "DID_RENEW"
NOTIFICATION_EXPIRED = "EXPIRED"
NOTIFICATION_DID_FAIL_TO_RENEW = "DID_FAIL_TO_RENEW"
NOTIFICATION_GRACE_PERIOD_EXPIRED = "GRACE_PERIOD_EXPIRED"
NOTIFICATION_REFUND = "REFUND"
NOTIFICATION_REVOKE = "REVOKE"

_GRANT_TYPES = (NOTIFICATION_SUBSCRIBED, NOTIFICATION_DID_RENEW)
_EXPIRE_TYPES = (
    NOTIFICATION_EXPIRED,
    NOTIFICATION_GRACE_PERIOD_EXPIRED,
    NOTIFICATION_REFUND,
    NOTIFICATION_REVOKE,
)

# Product ID → tier (match App Store Connect product IDs).
PRODUCT_TIER_MAP: dict[str, str] = {
    "com.airpromptskill.app.starter_biweekly": "starter",
    "com.airpromptskill.app.pro_biweekly": "pro",
}


@lru_cache(maxsize=1)
def _verifiers() -> tuple[SignedDataVerifier, SignedDataVerifier]:
    """Build (production, sandbox) verifiers once from the committed Apple root.

    Server misconfiguration raises RuntimeError so the router returns 500 and
    Apple retries — never ValueError, which the router maps to 400 (no retry).
    """
    try:
        if not settings.APPLE_BUNDLE_ID or not settings.APPLE_APP_APPLE_ID:
            raise RuntimeError(
                "Apple verifier misconfigured: APPLE_BUNDLE_ID and "
                "APPLE_APP_APPLE_ID are required"
            )
        certs = [_APPLE_ROOT_G3_PATH.read_bytes()]
        prod = SignedDataVerifier(
            certs, False, Environment.PRODUCTION,
            settings.APPLE_BUNDLE_ID, settings.APPLE_APP_APPLE_ID,
        )
        sandbox = SignedDataVerifier(
            certs, False, Environment.SANDBOX, settings.APPLE_BUNDLE_ID, None,
        )
        return prod, sandbox
    except (ValueError, OSError) as exc:
        # ValueError: prod verifier without app_apple_id / bad cert bytes.
        # OSError: cert asset missing/unreadable. Both are server faults → 500.
        raise RuntimeError(f"Apple verifier construction failed: {exc}") from exc


def _verify_notification(signed_payload: str):
    """Verify the outer notification: Production first, Sandbox only on
    INVALID_ENVIRONMENT. Returns (verifier, decoded_payload) so the SAME
    verifier decodes the nested transaction."""
    prod, sandbox = _verifiers()
    try:
        return prod, prod.verify_and_decode_notification(signed_payload)
    except VerificationException as exc:
        if exc.status == VerificationStatus.INVALID_ENVIRONMENT:
            return sandbox, sandbox.verify_and_decode_notification(signed_payload)
        raise


async def process_notification(signed_payload: str, db: AsyncSession) -> None:
    """Verify and dispatch an Apple App Store Server Notification.

    ValueError            → router 400 (bad Apple payload; no retry).
    RuntimeError / other  → router 500 (server fault; Apple retries).
    """
    try:
        verifier, payload = _verify_notification(signed_payload)
        signed_tx = payload.data.signedTransactionInfo if payload.data else None
        if not signed_tx:
            logger.warning(
                "Apple notification missing signedTransactionInfo — skipping"
            )
            return
        tx = verifier.verify_and_decode_signed_transaction(signed_tx)
    except VerificationException as exc:
        logger.warning("Apple notification verification failed: %s", exc.status.name)
        raise ValueError("Apple notification verification failed") from exc

    notification_type = payload.rawNotificationType
    signed_date_ms = payload.signedDate
    if not isinstance(signed_date_ms, int):
        raise ValueError("Apple notification missing signedDate")

    original_transaction_id = tx.originalTransactionId or ""
    app_account_token = tx.appAccountToken or ""
    product_id = tx.productId or ""
    expires_ms = tx.expiresDate
    tier = PRODUCT_TIER_MAP.get(product_id)

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

    # AP2 — drop strictly-older notifications, keyed on the outer signedDate.
    if (
        user.apple_last_signed_date is not None
        and signed_date_ms < user.apple_last_signed_date
    ):
        logger.info(
            "Apple %s: stale notification signedDate=%s < last=%s — skipping",
            notification_type, signed_date_ms, user.apple_last_signed_date,
        )
        return

    expires_at: datetime | None = (
        datetime.fromtimestamp(expires_ms / 1000, tz=timezone.utc)
        if expires_ms else None
    )

    try:
        if not user.apple_original_transaction_id:
            user.apple_original_transaction_id = original_transaction_id

        if notification_type in _GRANT_TYPES:
            if expires_at is None:
                # AP3 — never grant indefinite access; skip without recording.
                logger.warning(
                    "Apple %s without expiresDate — not granting", notification_type
                )
                return
            user.subscription_status = "active"
            user.subscription_expires_at = expires_at
            user.subscription_source = "apple"
            if tier:
                user.subscription_tier = tier

        elif notification_type == NOTIFICATION_DID_FAIL_TO_RENEW:
            if expires_at is None:  # AP3
                logger.warning(
                    "Apple DID_FAIL_TO_RENEW without expiresDate — skipping"
                )
                return
            user.subscription_status = "billing_retry"
            user.subscription_expires_at = expires_at

        elif notification_type in _EXPIRE_TYPES:
            user.subscription_status = "expired"
            user.subscription_expires_at = None

        else:
            logger.info(
                "Apple notification type %s — no action taken", notification_type
            )
            return

        user.apple_last_signed_date = signed_date_ms
        await db.commit()
        logger.info(
            "Apple %s applied: user=%s status=%s",
            notification_type, user.id, user.subscription_status,
        )
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.error(
            "Apple webhook DB error for txId=%s: %s", original_transaction_id, exc
        )
        raise


async def _resolve_user(
    db: AsyncSession, original_transaction_id: str, app_account_token: str
) -> User | None:
    """Resolve the user: first by apple_original_transaction_id (already linked),
    else by appAccountToken (the user id the iOS app set on the StoreKit
    purchase), establishing the link on the first notification."""
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
