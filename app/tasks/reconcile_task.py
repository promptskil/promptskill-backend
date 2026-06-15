"""Subscription reconciliation task — backstop for missed Stripe webhooks.

Runs daily (beat). For each Stripe-managed user whose subscription_expires_at
has passed, re-fetch the subscription from Stripe (the source of truth) and
re-sync subscription_status / subscription_expires_at. Catches a renewal or
cancel webhook that never reached us. Grandfathered / Apple users (no
stripe_subscription_id) are untouched — they expire via the access gate.

Engine: app.database_sync.sync_engine (sync, like purge_task). Status +
period-end mapping is shared with the webhook (STRIPE_STATUS_MAP,
extract_period_end) so both stay identical.
"""
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from app.database_sync import sync_engine
from app.services.stripe_client import stripe
from app.services.stripe_webhook_service import (
    STRIPE_STATUS_MAP,
    extract_period_end,
)
from app.tasks.celery_app import celery_app

try:
    import sentry_sdk
except ImportError:  # pragma: no cover — Sentry optional in tests
    sentry_sdk = None

logger = logging.getLogger(__name__)


def _breadcrumb(message: str) -> None:
    if sentry_sdk is None:
        return
    try:
        sentry_sdk.add_breadcrumb(message=message, category="reconcile")
    except Exception as exc:  # pragma: no cover
        logger.warning("sentry_breadcrumb_failed", extra={"error": str(exc)})


def _to_plain_dict(obj) -> dict:
    """Stripe SDK object -> plain nested dict (its .get() is unreliable)."""
    if isinstance(obj, dict):
        return obj
    return json.loads(str(obj))  # StripeObject.__str__ is JSON


@celery_app.task(name="reconcile_subscriptions_task")
def reconcile_subscriptions_task() -> dict:
    """Re-sync stale Stripe subscriptions from Stripe (the source of truth)."""
    reconciled = 0
    errors = 0
    with sync_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, stripe_subscription_id FROM users "
                "WHERE stripe_subscription_id IS NOT NULL "
                "AND subscription_expires_at IS NOT NULL "
                "AND subscription_expires_at < NOW()"
            )
        ).fetchall()

        for user_id, sub_id in rows:
            try:
                sub = _to_plain_dict(stripe.Subscription.retrieve(sub_id))
            except Exception as exc:  # noqa: BLE001 — skip one, keep going
                errors += 1
                logger.warning(
                    "reconcile: Stripe retrieve failed sub=%s: %s", sub_id, exc
                )
                continue

            new_status = STRIPE_STATUS_MAP.get(sub.get("status", ""), "expired")
            period_end = extract_period_end(sub)
            expires_at = (
                datetime.fromtimestamp(period_end, tz=timezone.utc)
                if period_end
                else None
            )
            conn.execute(
                text(
                    "UPDATE users SET subscription_status = :st, "
                    "subscription_expires_at = :exp WHERE id = :uid"
                ),
                {"st": new_status, "exp": expires_at, "uid": user_id},
            )
            reconciled += 1

        conn.commit()

    result = {"reconciled": reconciled, "errors": errors}
    _breadcrumb(f"Reconcile: {reconciled} synced, {errors} errors")
    logger.info("reconcile_complete", extra=result)
    return result
