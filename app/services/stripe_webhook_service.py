"""Stripe webhook service — verify + map subscription events to access state.

The signature-verified Stripe event is the single source of truth for web
subscription access. construct_event raises on a bad signature (caller returns
400, no retry). Stripe subscription.status maps to our subscription_status;
current_period_end maps to subscription_expires_at. The user is resolved via
stripe_customer_id (the link established at checkout).
"""
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User
from app.services.stripe_client import stripe

logger = logging.getLogger(__name__)

# Stripe subscription.status -> our subscription_status
STRIPE_STATUS_MAP = {
    "trialing": "trialing",
    "active": "active",
    "past_due": "billing_retry",
    "unpaid": "expired",
    "canceled": "expired",
    "incomplete": "expired",
    "incomplete_expired": "expired",
    "paused": "expired",
}

_SUBSCRIPTION_EVENTS = {
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
}


async def process_event(
    payload: bytes, sig_header: str, db: AsyncSession
) -> None:
    """Verify and dispatch a Stripe webhook event.

    Raises ValueError on a bad signature/payload (caller returns 400).
    """
    try:
        stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except Exception as exc:  # noqa: BLE001 — any verify failure -> 400
        logger.warning("Stripe webhook signature verification failed: %s", exc)
        raise ValueError("Invalid Stripe signature") from exc

    # Signature verified above. Read fields from plain JSON — the Stripe SDK
    # objects do not expose dict-style .get() in this version.
    event = json.loads(payload)
    event_type = event["type"]
    if event_type not in _SUBSCRIPTION_EVENTS:
        logger.info("Stripe event %s — no action taken", event_type)
        return

    sub = event["data"]["object"]
    customer_id = sub.get("customer")
    if not customer_id:
        logger.warning("Stripe %s missing customer — skipping", event_type)
        return

    result = await db.execute(
        select(User).where(User.stripe_customer_id == customer_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        logger.warning(
            "Stripe %s: no user for customer=%s", event_type, customer_id
        )
        return

    if event_type == "customer.subscription.deleted":
        new_status = "expired"
    else:
        new_status = STRIPE_STATUS_MAP.get(sub.get("status", ""), "expired")

    expires_at = None
    period_end = sub.get("current_period_end")
    if not period_end:
        # Newer API versions (e.g. dahlia) moved the period end onto items.
        items = (sub.get("items") or {}).get("data") or []
        if items:
            period_end = items[0].get("current_period_end")
    if not period_end:
        # Trialing subscriptions: the access boundary is the trial end.
        period_end = sub.get("trial_end")
    if period_end:
        expires_at = datetime.fromtimestamp(period_end, tz=timezone.utc)

    user.subscription_status = new_status
    user.subscription_expires_at = expires_at
    user.subscription_source = "stripe"
    user.stripe_subscription_id = sub.get("id")

    await db.commit()
    logger.info(
        "Stripe %s: user=%s status=%s expires=%s",
        event_type,
        user.id,
        new_status,
        expires_at,
    )
