"""Billing router — Stripe Checkout for individual web subscriptions.

POST /billing/checkout — auth required (NOT subscription-gated; this is how
an unsubscribed user starts a subscription). Returns the Checkout URL.
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.schemas import CheckoutResponse
from app.services import billing_service

router = APIRouter()


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CheckoutResponse:
    url = await billing_service.create_checkout_session(user_id, db)
    return CheckoutResponse(url=url)
