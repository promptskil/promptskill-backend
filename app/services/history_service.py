"""History service — Phase 7 — Steps 7.2.

Three async operations for post-generate lifecycle:

  record_feedback(user_id, prompt_id, vote, db)
    — set feedback_vote on a prompt the user owns. 404 on any miss.

  get_history(user_id, limit, offset, db)
    — paginated list of the user's non-soft-deleted prompts,
      ordered by created_at DESC. Returns (items, total).

  soft_delete_prompt(user_id, prompt_id, db)
    — sets deleted_at. 404 on any miss (wrong user, already deleted,
      or nonexistent — all collapse to one response).

Security contract: 404-not-403 on all cross-user or soft-deleted
access — prevents leaking prompt_id existence. This is enforced by
the WHERE clause on every SELECT (`user_id = ... AND deleted_at IS NULL`).
"""
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prompt import Prompt, PromptVote


_MAX_HISTORY_LIMIT = 50


# ─────────────────────── record_feedback ─────────────────────────────────

async def record_feedback(
    user_id: UUID,
    prompt_id: UUID,
    vote: str,  # Literal "up" | "down" validated at schema layer
    db: AsyncSession,
) -> dict:
    """Set feedback_vote on a prompt owned by user_id.

    Raises HTTPException 404 if:
      - prompt does not exist
      - prompt belongs to a different user
      - prompt is soft-deleted
    """
    prompt = (
        await db.execute(
            select(Prompt).where(
                Prompt.id == prompt_id,
                Prompt.user_id == user_id,
                Prompt.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()

    if prompt is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "prompt not found"},
        )

    prompt.feedback_vote = PromptVote(vote)
    await db.commit()
    await db.refresh(prompt)
    return {"prompt_id": str(prompt.id), "vote": prompt.feedback_vote.value}


# ─────────────────────── get_history ─────────────────────────────────────

async def get_history(
    user_id: UUID,
    limit: int,
    offset: int,
    db: AsyncSession,
) -> dict:
    """Return paginated non-deleted prompts for user, DESC by created_at.

    Raises HTTPException 400 on limit/offset out of range.
    """
    if limit < 1 or limit > _MAX_HISTORY_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_limit",
                "message": f"limit must be 1..{_MAX_HISTORY_LIMIT}",
            },
        )
    if offset < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_offset", "message": "offset must be >= 0"},
        )

    base_filter = (
        Prompt.user_id == user_id,
        Prompt.deleted_at.is_(None),
    )

    total = (
        await db.execute(
            select(func.count()).select_from(Prompt).where(*base_filter)
        )
    ).scalar_one()

    rows = (
        await db.execute(
            select(Prompt)
            .where(*base_filter)
            .order_by(Prompt.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    items = [
        {
            "prompt_id": str(r.id),
            "model": r.model,
            "topic": r.topic,
            "prompt_text": r.prompt_text,
            "system_prompt_version": r.system_prompt_version,
            "app_version": r.app_version,
            "feedback_vote": r.feedback_vote.value if r.feedback_vote else None,
            "created_at": r.created_at,
        }
        for r in rows
    ]

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ─────────────────────── soft_delete_prompt ──────────────────────────────

async def soft_delete_prompt(
    user_id: UUID,
    prompt_id: UUID,
    db: AsyncSession,
) -> dict:
    """Set deleted_at on a prompt owned by user_id. 404 on any miss.

    Stewardship (Matthew 25:14-30): never DROP — preserve the row.
    """
    prompt = (
        await db.execute(
            select(Prompt).where(
                Prompt.id == prompt_id,
                Prompt.user_id == user_id,
                Prompt.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()

    if prompt is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "prompt not found"},
        )

    # Naive datetime to match the existing `DateTime` column (no tz).
    # Phase 15 hygiene: migrate utcnow() → datetime.now(UTC) + column tz.
    prompt.deleted_at = datetime.utcnow()
    await db.commit()
    await db.refresh(prompt)
    return {
        "prompt_id": str(prompt.id),
        "deleted_at": prompt.deleted_at,
    }
