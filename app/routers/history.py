"""History router — Phase 7 — Step 7.3.

Three endpoints, all auth-required:

  PATCH /user/feedback                 — record feedback_vote
  GET   /history?limit=&offset=        — list non-deleted prompts
  PATCH /prompts/{prompt_id}/delete    — soft delete

Security contract: 404-not-403 on cross-user or soft-deleted access
(enforced in history_service WHERE clauses — not here).
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.schemas import (
    DeleteResponse,
    FeedbackRequest,
    FeedbackResponse,
    HistoryResponse,
)
from app.services import history_service

router = APIRouter()


@router.patch("/user/feedback", response_model=FeedbackResponse)
async def feedback(
    body: FeedbackRequest,
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeedbackResponse:
    result = await history_service.record_feedback(
        user_id=user_id,
        prompt_id=body.prompt_id,
        vote=body.vote,
        db=db,
    )
    return FeedbackResponse(
        prompt_id=UUID(result["prompt_id"]),
        vote=result["vote"],
    )


@router.get("/history", response_model=HistoryResponse)
async def history(
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HistoryResponse:
    result = await history_service.get_history(
        user_id=user_id,
        limit=limit,
        offset=offset,
        db=db,
    )
    return HistoryResponse(**result)


@router.patch("/prompts/{prompt_id}/delete", response_model=DeleteResponse)
async def delete_prompt(
    prompt_id: UUID,
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DeleteResponse:
    result = await history_service.soft_delete_prompt(
        user_id=user_id,
        prompt_id=prompt_id,
        db=db,
    )
    return DeleteResponse(
        prompt_id=UUID(result["prompt_id"]),
        deleted_at=result["deleted_at"],
    )
