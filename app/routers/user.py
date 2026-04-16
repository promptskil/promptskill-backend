"""User router — Phase 8 — Step 8.2.

Two endpoints, both auth-required:

  GET   /user              — read authenticated user's profile
  PATCH /user/email        — change authenticated user's email

Returns UserResponse {id, email} on both. 409 on duplicate email.
Session invalidation on email-change is NOT performed (spec-silent
default — see user_service.update_email docstring).
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.schemas import UpdateEmailRequest, UserResponse
from app.services import user_service

router = APIRouter()


@router.get("/user", response_model=UserResponse)
async def get_user(
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    result = await user_service.get_user(user_id=user_id, db=db)
    return UserResponse(id=result["id"], email=result["email"])


@router.patch("/user/email", response_model=UserResponse)
async def update_email(
    body: UpdateEmailRequest,
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    result = await user_service.update_email(
        user_id=user_id,
        new_email=body.email,
        db=db,
    )
    return UserResponse(id=result["id"], email=result["email"])
