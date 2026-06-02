"""Business router — Phase 4, Step 6.

Five endpoints, all auth-required, 10/hour rate limit:

  GET    /business/mine — caller's business + members (any member)
  POST   /business/create — create org
  POST   /business/{business_id}/invite — invite member (admin only)
  DELETE /business/{business_id}/members/{target_user_id} — remove member (admin only)
  GET    /business/{business_id}/history — org prompt history (admin only)
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.rate_limit import limiter
from app.schemas import (
    BusinessHistoryResponse,
    BusinessInviteResponse,
    BusinessMineResponse,
    InviteBusinessMemberRequest,
    RemoveMemberResponse,
)
from app.services import business_service

router = APIRouter()


@router.get("/mine", response_model=BusinessMineResponse)
@limiter.limit("60/minute")
async def my_business(
    request: Request,
    response: Response,  # REQUIRED by slowapi when headers_enabled=True
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BusinessMineResponse:
    result = await business_service.get_my_business(
        user_id=user_id,
        db=db,
    )
    return BusinessMineResponse(**result)


@router.post("/{business_id}/invite", response_model=BusinessInviteResponse)
@limiter.limit("10/hour")
async def invite_member(
    request: Request,
    response: Response,  # REQUIRED by slowapi when headers_enabled=True
    business_id: UUID,
    body: InviteBusinessMemberRequest,
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BusinessInviteResponse:
    result = await business_service.invite_member(
        business_id=business_id,
        admin_user_id=user_id,
        email=body.email,
        role=body.role,
        db=db,
    )
    return BusinessInviteResponse(**result)


@router.delete(
    "/{business_id}/members/{target_user_id}",
    response_model=RemoveMemberResponse,
)
@limiter.limit("10/hour")
async def remove_member(
    request: Request,
    response: Response,  # REQUIRED by slowapi when headers_enabled=True
    business_id: UUID,
    target_user_id: UUID,
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RemoveMemberResponse:
    result = await business_service.remove_member(
        business_id=business_id,
        admin_user_id=user_id,
        target_user_id=target_user_id,
        db=db,
    )
    return RemoveMemberResponse(**result)


@router.get("/{business_id}/history", response_model=BusinessHistoryResponse)
@limiter.limit("10/hour")
async def business_history(
    request: Request,
    response: Response,  # REQUIRED by slowapi when headers_enabled=True
    business_id: UUID,
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BusinessHistoryResponse:
    result = await business_service.get_business_history(
        business_id=business_id,
        admin_user_id=user_id,
        limit=limit,
        offset=offset,
        db=db,
    )
    return BusinessHistoryResponse(**result)
