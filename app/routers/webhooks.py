"""
POST /webhooks/apple — Apple App Store Server Notifications v2

Apple POSTs a JSON body: { "signedPayload": "<JWS string>" }
on every subscription lifecycle event.

Response contract (Apple requirement):
  200 → event received and processed
  4xx → bad payload (Apple will NOT retry)
  5xx → processing error (Apple WILL retry up to 3x with backoff)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.apple_webhook_service import process_notification

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class AppleNotificationPayload(BaseModel):
    signedPayload: str


@router.post(
    "/apple",
    status_code=status.HTTP_200_OK,
    summary="Apple App Store Server Notification",
)
async def apple_webhook(
    payload: AppleNotificationPayload,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Receive and process Apple App Store Server Notification v2.

    - Verifies the JWS signed payload against Apple's certificate chain.
    - Dispatches subscription lifecycle events to update user subscription state.
    - Returns 200 on success, 400 on bad payload, 500 on DB error
      (triggers Apple retry).
    """
    try:
        await process_notification(payload.signedPayload, db)
    except ValueError as exc:
        # Bad payload — do not retry
        logger.warning("Apple webhook rejected (400): %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        # DB / unexpected error — Apple will retry
        logger.error("Apple webhook internal error (500): %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal processing error",
        ) from exc

    return {"received": True}
