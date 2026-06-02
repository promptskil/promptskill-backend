"""Invite acceptance router.

Single endpoint: POST /invite/accept
  Body:    AcceptInviteRequest{token, password}
  Auth:    none — the invited email has no prior account; accepting
           creates the employee account and logs them in.
  Returns: AcceptInviteResponse{token, user_id, account_type, business_id}
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import AcceptInviteRequest, AcceptInviteResponse
from app.services import business_service

router = APIRouter()


@router.post("/invite/accept", response_model=AcceptInviteResponse)
async def accept_invite(
    body: AcceptInviteRequest,
    db: AsyncSession = Depends(get_db),
) -> AcceptInviteResponse:
    result = await business_service.accept_invite(
        token=body.token,
        password=body.password,
        db=db,
    )
    return AcceptInviteResponse(**result)
