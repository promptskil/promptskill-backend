"""Invite acceptance router — Phase 0 Step 0.3.

Single endpoint: POST /invite/accept
  Body:    AcceptInviteRequest{token}
  Auth:    Bearer required (must be the invited user)
  Returns: AcceptInviteResponse{business_id, role}
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.schemas import AcceptInviteRequest, AcceptInviteResponse
from app.services import business_service

router = APIRouter()


@router.post("/invite/accept", response_model=AcceptInviteResponse)
async def accept_invite(
    body: AcceptInviteRequest,
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AcceptInviteResponse:
    result = await business_service.accept_invite(
        token=body.token,
        user_id=user_id,
        db=db,
    )
    return AcceptInviteResponse(**result)
