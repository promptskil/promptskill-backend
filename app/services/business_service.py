"""Business service — Phase 3, Step 5.

Six async operations:

  create_business(user_id, name, db)
    — create org + add owner as admin member. 409 if user already owns one.

  invite_member(business_id, admin_user_id, email, role, db)
    — admin-only. Locks business row (FOR UPDATE) to prevent seat limit
      race conditions. Normalizes email. Checks seat limit, duplicate
      membership, duplicate pending invite.

  remove_member(business_id, admin_user_id, target_user_id, db)
    — admin-only. Cannot remove the business owner. Prevents last-admin
      self-removal to avoid operational orphaning.

  get_business_history(business_id, admin_user_id, limit, offset, db)
    — admin-only. Paginated prompts for current members only.
      DOCUMENTED BEHAVIOR: removed members' prompts are no longer
      visible in business history. Membership deletion = full exit.
      Soft-delete on business_members is the Phase N fix if audit
      retention is required.

  get_my_business(user_id, db)
    — any authenticated member. Returns caller's business + full member
      list. Dashboard bootstrap endpoint. 404 if no membership exists.

  _require_admin(business_id, user_id, db)
    — internal. 404 on missing business, 403 on non-admin caller.
"""

import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import Business
from app.models.business_invite import BusinessInvite
from app.models.business_member import BusinessMember
from app.models.prompt import Prompt
from app.models.user import User
from app.tasks.business_invite_email_task import send_business_invite_email_task

INVITE_LIFETIME_HOURS = 72
_MAX_HISTORY_LIMIT = 50


# ─────────────────────── internal helpers ────────────────────────────────

async def _require_admin(
    business_id: UUID,
    user_id: UUID,
    db: AsyncSession,
) -> Business:
    """Return the Business if user_id is an admin member. Raise otherwise.

    404 if business does not exist — prevents leaking business existence
    to non-members. 403 if caller is a member but not admin.
    """
    business = (
        await db.execute(
            select(Business).where(Business.id == business_id)
        )
    ).scalar_one_or_none()

    if business is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "business not found"},
        )

    membership = (
        await db.execute(
            select(BusinessMember).where(
                BusinessMember.business_id == business_id,
                BusinessMember.user_id == user_id,
                BusinessMember.role == "admin",
            )
        )
    ).scalar_one_or_none()

    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "forbidden", "message": "admin access required"},
        )

    return business


# ─────────────────────── create_business ─────────────────────────────────

async def create_business(
    user_id: UUID,
    name: str,
    db: AsyncSession,
) -> dict:
    """Create a business and add the creator as admin member.

    409 if the user already owns a business.
    """
    existing = (
        await db.execute(
            select(Business).where(Business.owner_id == user_id)
        )
    ).scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "business_exists",
                "message": "You already own a business",
            },
        )

    business = Business(owner_id=user_id, name=name)
    db.add(business)
    await db.flush()  # populate business.id

    member = BusinessMember(
        business_id=business.id,
        user_id=user_id,
        role="admin",
    )
    db.add(member)
    await db.commit()
    await db.refresh(business)

    return {
        "id": business.id,
        "name": business.name,
        "owner_id": business.owner_id,
        "seat_limit": business.seat_limit,
        "created_at": business.created_at,
    }


# ─────────────────────── invite_member ───────────────────────────────────

async def invite_member(
    business_id: UUID,
    admin_user_id: UUID,
    email: str,
    role: str,
    db: AsyncSession,
) -> dict:
    """Send an invite to email. Admin-only.

    Locks the Business row (FOR UPDATE) before seat count check to
    prevent concurrent oversubscription.

    Raises:
      403 if caller is not admin
      400 if seat limit is reached
      409 if email is already a member
      409 if a pending unexpired invite already exists for this email
    """
    normalized_email = email.lower().strip()

    # Lock business row — serializes concurrent invite operations
    business = (
        await db.execute(
            select(Business).where(Business.id == business_id).with_for_update()
        )
    ).scalar_one_or_none()

    if business is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "business not found"},
        )

    # Admin check against locked row
    membership = (
        await db.execute(
            select(BusinessMember).where(
                BusinessMember.business_id == business_id,
                BusinessMember.user_id == admin_user_id,
                BusinessMember.role == "admin",
            )
        )
    ).scalar_one_or_none()

    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "forbidden", "message": "admin access required"},
        )

    # Seat limit check — count after lock acquired
    member_count = (
        await db.execute(
            select(func.count()).select_from(BusinessMember).where(
                BusinessMember.business_id == business_id
            )
        )
    ).scalar_one()

    if member_count >= business.seat_limit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "seat_limit_reached",
                "message": f"Seat limit of {business.seat_limit} reached",
            },
        )

    # Check email is not already a member
    target_user = (
        await db.execute(
            select(User).where(User.email == normalized_email)
        )
    ).scalar_one_or_none()

    if target_user:
        already_member = (
            await db.execute(
                select(BusinessMember).where(
                    BusinessMember.business_id == business_id,
                    BusinessMember.user_id == target_user.id,
                )
            )
        ).scalar_one_or_none()

        if already_member:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "already_member",
                    "message": "This user is already a member",
                },
            )

    # Check no pending unexpired invite for this email+business
    now = datetime.now(timezone.utc)
    pending_invite = (
        await db.execute(
            select(BusinessInvite).where(
                BusinessInvite.business_id == business_id,
                BusinessInvite.email == normalized_email,
                BusinessInvite.expires_at > now,
            )
        )
    ).scalar_one_or_none()

    if pending_invite:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "invite_pending",
                "message": "A pending invite already exists for this email",
            },
        )

    token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(hours=INVITE_LIFETIME_HOURS)

    invite = BusinessInvite(
        business_id=business_id,
        invited_by_id=admin_user_id,
        email=normalized_email,
        token=token,
        role=role,
        expires_at=expires_at,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    send_business_invite_email_task.delay(
        email=normalized_email,
        org_name=business.name,
        token=token,
        role=role,
    )

    return {
        "id": invite.id,
        "email": invite.email,
        "role": invite.role,
        "expires_at": invite.expires_at,
        "created_at": invite.created_at,
    }


# ─────────────────────── remove_member ───────────────────────────────────

async def remove_member(
    business_id: UUID,
    admin_user_id: UUID,
    target_user_id: UUID,
    db: AsyncSession,
) -> dict:
    """Remove a member from the business. Admin-only.

    Raises:
      403 if caller is not admin
      400 if target is the business owner
      400 if caller is the last admin and is removing themselves
      404 if target is not a member
    """
    business = await _require_admin(business_id, admin_user_id, db)

    if target_user_id == business.owner_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "cannot_remove_owner",
                "message": "The business owner cannot be removed",
            },
        )

    # Last-admin self-removal protection
    if target_user_id == admin_user_id:
        admin_count = (
            await db.execute(
                select(func.count()).select_from(BusinessMember).where(
                    BusinessMember.business_id == business_id,
                    BusinessMember.role == "admin",
                )
            )
        ).scalar_one()

        if admin_count == 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "last_admin",
                    "message": "Cannot remove the only admin from the business",
                },
            )

    membership = (
        await db.execute(
            select(BusinessMember).where(
                BusinessMember.business_id == business_id,
                BusinessMember.user_id == target_user_id,
            )
        )
    ).scalar_one_or_none()

    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "member not found"},
        )

    await db.execute(
        delete(BusinessMember).where(
            BusinessMember.business_id == business_id,
            BusinessMember.user_id == target_user_id,
        )
    )
    await db.commit()

    return {"user_id": target_user_id, "removed": True}


# ─────────────────────── get_business_history ────────────────────────────

async def get_business_history(
    business_id: UUID,
    admin_user_id: UUID,
    limit: int,
    offset: int,
    db: AsyncSession,
) -> dict:
    """Paginated prompts for current members of the business. Admin-only.

    DOCUMENTED BEHAVIOR: prompts from removed members are not included.
    Membership deletion = full exit from business history visibility.

    Raises 400 on invalid limit/offset.
    """
    await _require_admin(business_id, admin_user_id, db)

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
        BusinessMember.business_id == business_id,
        Prompt.deleted_at.is_(None),
    )

    total = (
        await db.execute(
            select(func.count())
            .select_from(Prompt)
            .join(BusinessMember, Prompt.user_id == BusinessMember.user_id)
            .where(*base_filter)
        )
    ).scalar_one()

    rows = (
        await db.execute(
            select(Prompt)
            .join(BusinessMember, Prompt.user_id == BusinessMember.user_id)
            .where(*base_filter)
            .order_by(Prompt.created_at.desc(), Prompt.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    items = [
        {
            "prompt_id": r.id,
            "user_id": r.user_id,
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


# ─────────────────────── get_my_business ─────────────────────────────────

async def get_my_business(user_id: UUID, db: AsyncSession) -> dict:
    """Return the caller's business and full member list.

    Finds the business via the caller's membership row — works for
    owners and invited members alike. 404 if no membership exists.
    """
    business = (
        await db.execute(
            select(Business)
            .join(BusinessMember, Business.id == BusinessMember.business_id)
            .where(BusinessMember.user_id == user_id)
        )
    ).scalar_one_or_none()

    if business is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "not_found",
                "message": "No business found for this user",
            },
        )

    member_rows = (
        await db.execute(
            select(BusinessMember, User.email)
            .join(User, BusinessMember.user_id == User.id)
            .where(BusinessMember.business_id == business.id)
        )
    ).all()

    members = [
        {
            "user_id": m.user_id,
            "email": email,
            "role": m.role,
            "joined_at": m.joined_at,
        }
        for m, email in member_rows
    ]

    return {
        "id": business.id,
        "name": business.name,
        "owner_id": business.owner_id,
        "seat_limit": business.seat_limit,
        "members": members,
        "created_at": business.created_at,
    }


# ─────────────────────── accept_invite ───────────────────────────────────

async def accept_invite(
    token: str,
    user_id: UUID,
    db: AsyncSession,
) -> dict:
    """Accept a business invite. Authenticated endpoint.

    Raises:
      404 if token is invalid
      410 if invite expired (>72hr per INVITE_LIFETIME_HOURS)
      403 if authenticated user's email does not match invite.email
      409 if user is already a member of this business
      400 if seat limit reached (race condition guard)
    """
    invite = (
        await db.execute(
            select(BusinessInvite).where(BusinessInvite.token == token)
        )
    ).scalar_one_or_none()

    if invite is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "invite not found"},
        )

    now = datetime.now(timezone.utc)
    if invite.expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"error": "expired", "message": "invite has expired"},
        )

    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()

    if user is None or user.email.lower().strip() != invite.email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "email_mismatch",
                "message": "invite was sent to a different email",
            },
        )

    already_member = (
        await db.execute(
            select(BusinessMember).where(
                BusinessMember.business_id == invite.business_id,
                BusinessMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()

    if already_member:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "already_member",
                "message": "You are already a member of this business",
            },
        )

    business = (
        await db.execute(
            select(Business)
            .where(Business.id == invite.business_id)
            .with_for_update()
        )
    ).scalar_one_or_none()

    if business is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "not_found",
                "message": "business no longer exists",
            },
        )

    member_count = (
        await db.execute(
            select(func.count()).select_from(BusinessMember).where(
                BusinessMember.business_id == invite.business_id
            )
        )
    ).scalar_one()

    if member_count >= business.seat_limit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "seat_limit_reached",
                "message": f"Seat limit of {business.seat_limit} reached",
            },
        )

    member = BusinessMember(
        business_id=invite.business_id,
        user_id=user_id,
        role=invite.role,
    )
    db.add(member)

    await db.execute(
        delete(BusinessInvite).where(BusinessInvite.id == invite.id)
    )

    await db.commit()

    return {
        "business_id": invite.business_id,
        "role": invite.role,
    }
