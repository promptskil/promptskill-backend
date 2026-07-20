"""Billing service — Stripe Checkout for individual web subscriptions.

create_checkout_session ensures the user has a Stripe customer (the durable
user <-> Stripe identity link the webhook relies on), then creates a
subscription Checkout Session with a free trial and card required up front.
"""
import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User
from app.services.stripe_client import stripe


async def create_checkout_session(user_id: UUID, db: AsyncSession) -> str:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise ValueError("User not found")

    # Ensure a Stripe customer exists; reuse if we already have one.
    if not user.stripe_customer_id:
        customer = await asyncio.to_thread(
            stripe.Customer.create,
            email=user.email,
            metadata={"user_id": str(user.id)},
        )
        user.stripe_customer_id = customer["id"]
        await db.commit()

    session = await asyncio.to_thread(
        stripe.checkout.Session.create,
        mode="subscription",
        customer=user.stripe_customer_id,
        line_items=[{"price": settings.STRIPE_PRICE_ID, "quantity": 1}],
        subscription_data={"trial_period_days": settings.STRIPE_TRIAL_DAYS},
        # Card required up front even during the trial (Decision 6a).
        payment_method_collection="always",
        success_url=f"{settings.WEB_BASE_URL}/?checkout=success",
        cancel_url=f"{settings.WEB_BASE_URL}/?checkout=cancel",
        client_reference_id=str(user.id),
    )
    return session["url"]
