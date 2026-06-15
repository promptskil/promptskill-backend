"""Stripe webhook router — POST /webhooks/stripe.

Public; the signature is verified inside the service against the RAW request
body, so this handler reads request.body() directly (not a Pydantic model).
Separate from the Apple webhook router.

Response contract:
  200 -> received/processed
  400 -> bad signature/payload (Stripe will NOT retry)
  500 -> processing error (Stripe WILL retry)
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import stripe_webhook_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/stripe", status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    payload = await request.body()  # RAW bytes — required for signature
    sig_header = request.headers.get("stripe-signature", "")
    try:
        await stripe_webhook_service.process_event(payload, sig_header, db)
    except ValueError as exc:
        logger.warning("Stripe webhook rejected (400): %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001 — processing error -> 500 retry
        logger.error("Stripe webhook internal error (500): %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal processing error",
        ) from exc
    return {"received": True}
