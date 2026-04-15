"""User service — Phase 8 — Step 8.1.

Two async operations for authenticated self-management:

  get_user(user_id, db)
    — return {id, email} for the authenticated user. 404 if missing.

  update_email(user_id, new_email, db)
    — change the user's email. 409 if the new email is already taken
      (uniqueness enforced at DB level by the users.email UNIQUE index
      from Phase 1 — we catch IntegrityError). 404 if user not found.

Design notes:
  - Email format is validated at the schema layer (EmailStr).
  - No session invalidation on email change — spec-silent default.
  - Password changes are out of scope (happen via /auth/reset-password).
"""
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


# ─────────────────────── get_user ────────────────────────────────────────

async def get_user(user_id: UUID, db: AsyncSession) -> dict:
    """Return {id, email} for the authenticated user.

    Raises HTTPException 404 if the user row is missing (should be
    rare — auth middleware resolved the token to this user_id, but
    the row may have been deleted between auth and service call).
    """
    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "user not found"},
        )

    return {"id": user.id, "email": user.email}


# ─────────────────────── update_email ────────────────────────────────────

async def update_email(
    user_id: UUID,
    new_email: str,
    db: AsyncSession,
) -> dict:
    """Change the user's email.

    Raises:
      - 404 if user does not exist
      - 409 if new_email is already taken by another user
        (detected via IntegrityError on commit — race-safe versus
        a plain SELECT-then-UPDATE pre-check).
    """
    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "user not found"},
        )

    user.email = new_email
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "email_taken", "message": "email already in use"},
        )
    await db.refresh(user)
    return {"id": user.id, "email": user.email}
