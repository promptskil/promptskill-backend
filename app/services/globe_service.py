"""Globe service — runtime rules for the Globe subsystem.

  get_me            — current user's username or None (gate check).
  claim_username    — one-time claim; 409 if taken or already set.
  _assert_profile   — 403 username_required if no profile (contribution gate).
  create_zone/post/reply — require a profile; create_reply validates that a
                    parent_reply_id belongs to the same post (400).
  get_zones         — chronological; q searches zone titles + posts + replies
                    -> DISTINCT matching zones.
  get_thread        — posts + flat replies (client nests by parent_reply_id).

Errors follow the repo contract: HTTPException {error, message}; 404-not-403
on missing zone/post. Browsing (reads) requires no profile.
"""
import base64
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import exists, or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.globe import (
    GlobeDomain,
    GlobeHiddenZone,
    GlobePost,
    GlobeProfile,
    GlobeReply,
    GlobeZone,
)

_MAX_LIMIT = 50


def _validate_page(limit: int, offset: int) -> None:
    if limit < 1 or limit > _MAX_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_limit",
                "message": f"limit must be 1..{_MAX_LIMIT}",
            },
        )
    if offset < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_offset", "message": "offset must be >= 0"},
        )


def _like_escape(term: str) -> str:
    # Treat user input literally: escape LIKE wildcards.
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ─────────────────────── profile / username ──────────────────────────────

async def get_me(user_id: UUID, db: AsyncSession) -> str | None:
    profile = (
        await db.execute(
            select(GlobeProfile).where(GlobeProfile.user_id == user_id)
        )
    ).scalar_one_or_none()
    return profile.username if profile else None


async def claim_username(user_id: UUID, username: str, db: AsyncSession) -> str:
    existing = (
        await db.execute(
            select(GlobeProfile).where(GlobeProfile.user_id == user_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "username_exists", "message": "username already set"},
        )
    taken = (
        await db.execute(
            select(GlobeProfile).where(GlobeProfile.username == username)
        )
    ).scalar_one_or_none()
    if taken is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "username_taken", "message": "that username is taken"},
        )
    profile = GlobeProfile(user_id=user_id, username=username)
    db.add(profile)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "username_taken", "message": "that username is taken"},
        )
    await db.refresh(profile)
    return profile.username


async def _assert_profile(user_id: UUID, db: AsyncSession) -> GlobeProfile:
    profile = (
        await db.execute(
            select(GlobeProfile).where(GlobeProfile.user_id == user_id)
        )
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "username_required",
                "message": "create a username first",
            },
        )
    return profile


# ─────────────────────── domains (shared, deduped) ───────────────────────

def _normalize_domain(name: str) -> str:
    n = name.strip()
    return (n[:1].upper() + n[1:]) if n else n


async def get_or_create_domain(name: str, db: AsyncSession) -> str:
    canonical = _normalize_domain(name)
    found = (
        await db.execute(
            select(GlobeDomain.name).where(GlobeDomain.name == canonical)
        )
    ).scalar_one_or_none()
    if found is not None:
        return found
    db.add(GlobeDomain(name=canonical))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        found = (
            await db.execute(
                select(GlobeDomain.name).where(GlobeDomain.name == canonical)
            )
        ).scalar_one_or_none()
        return found or canonical
    return canonical


async def list_domains(q: str | None, db: AsyncSession) -> dict:
    stmt = select(GlobeDomain.name)
    if q:
        stmt = stmt.where(
            GlobeDomain.name.ilike(f"%{_like_escape(q)}%", escape="\\")
        )
    rows = (
        await db.execute(stmt.order_by(GlobeDomain.name).limit(50))
    ).scalars().all()
    return {"domains": list(rows)}


def _encode_cursor(created_at: datetime, post_id: UUID) -> str:
    raw = f"{created_at.isoformat()}|{post_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts_str, id_str = raw.split("|", 1)
        return datetime.fromisoformat(ts_str), UUID(id_str)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "bad_cursor", "message": "invalid cursor"},
        ) from exc


async def get_feed(
    user_id: UUID, limit: int, cursor: str | None, db: AsyncSession
) -> dict:
    """Cross-zone post feed: top-level posts from all non-hidden zones,
    newest first, keyset-paginated on (created_at, id). Each row carries its
    zone title/domain + author username."""
    hidden = exists().where(
        (GlobeHiddenZone.user_id == user_id)
        & (GlobeHiddenZone.zone_id == GlobePost.zone_id)
    )
    stmt = (
        select(
            GlobePost.id,
            GlobePost.zone_id,
            GlobeZone.title,
            GlobeZone.domain,
            GlobeProfile.username,
            GlobePost.body,
            GlobePost.created_at,
        )
        .join(GlobeZone, GlobeZone.id == GlobePost.zone_id)
        .join(GlobeProfile, GlobeProfile.user_id == GlobePost.author_user_id)
        .where(~hidden)
    )
    if cursor:
        ts, cid = _decode_cursor(cursor)
        stmt = stmt.where(
            tuple_(GlobePost.created_at, GlobePost.id) < tuple_(ts, cid)
        )
    stmt = stmt.order_by(
        GlobePost.created_at.desc(), GlobePost.id.desc()
    ).limit(limit)
    rows = (await db.execute(stmt)).all()
    items = [
        {
            "post_id": r.id,
            "zone_id": r.zone_id,
            "zone_title": r.title,
            "zone_domain": r.domain,
            "author_username": r.username,
            "body": r.body,
            "created_at": r.created_at,
        }
        for r in rows
    ]
    next_cursor = (
        _encode_cursor(rows[-1].created_at, rows[-1].id)
        if len(rows) == limit
        else None
    )
    return {"items": items, "next_cursor": next_cursor}


# ─────────────────────── contributions ───────────────────────────────────

async def create_zone(
    user_id: UUID, domain: str, title: str, db: AsyncSession
) -> dict:
    await _assert_profile(user_id, db)
    canonical = await get_or_create_domain(domain, db)
    zone = GlobeZone(domain=canonical, title=title, author_user_id=user_id)
    db.add(zone)
    await db.commit()
    await db.refresh(zone)
    return {
        "id": zone.id,
        "domain": zone.domain,
        "title": zone.title,
        "created_at": zone.created_at,
    }


async def create_post(
    user_id: UUID, zone_id: UUID, body: str, db: AsyncSession
) -> dict:
    profile = await _assert_profile(user_id, db)
    exists = (
        await db.execute(select(GlobeZone.id).where(GlobeZone.id == zone_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "zone not found"},
        )
    post = GlobePost(zone_id=zone_id, author_user_id=user_id, body=body)
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return {
        "id": post.id,
        "author_username": profile.username,
        "body": post.body,
        "created_at": post.created_at,
        "replies": [],
    }


async def create_reply(
    user_id: UUID,
    post_id: UUID,
    body: str,
    parent_reply_id: UUID | None,
    db: AsyncSession,
) -> dict:
    profile = await _assert_profile(user_id, db)
    post_exists = (
        await db.execute(select(GlobePost.id).where(GlobePost.id == post_id))
    ).scalar_one_or_none()
    if post_exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "post not found"},
        )
    if parent_reply_id is not None:
        parent_post = (
            await db.execute(
                select(GlobeReply.post_id).where(
                    GlobeReply.id == parent_reply_id
                )
            )
        ).scalar_one_or_none()
        if parent_post is None or parent_post != post_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_parent",
                    "message": "parent reply is not in this post",
                },
            )
    reply = GlobeReply(
        post_id=post_id,
        parent_reply_id=parent_reply_id,
        author_user_id=user_id,
        body=body,
    )
    db.add(reply)
    await db.commit()
    await db.refresh(reply)
    return {
        "id": reply.id,
        "parent_reply_id": reply.parent_reply_id,
        "author_username": profile.username,
        "body": reply.body,
        "created_at": reply.created_at,
    }


# ─────────────────────── edits (author-only) ─────────────────────────────

async def edit_post(
    user_id: UUID, post_id: UUID, body: str, db: AsyncSession
) -> dict:
    post = (
        await db.execute(
            select(GlobePost).where(
                GlobePost.id == post_id,
                GlobePost.author_user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if post is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "post not found"},
        )
    post.body = body
    await db.commit()
    await db.refresh(post)
    return {"id": post.id, "body": post.body}


async def edit_reply(
    user_id: UUID, reply_id: UUID, body: str, db: AsyncSession
) -> dict:
    reply = (
        await db.execute(
            select(GlobeReply).where(
                GlobeReply.id == reply_id,
                GlobeReply.author_user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if reply is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "reply not found"},
        )
    reply.body = body
    await db.commit()
    await db.refresh(reply)
    return {"id": reply.id, "body": reply.body}


async def delete_post(user_id: UUID, post_id: UUID, db: AsyncSession) -> dict:
    post = (
        await db.execute(
            select(GlobePost).where(
                GlobePost.id == post_id,
                GlobePost.author_user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if post is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "post not found"},
        )
    await db.delete(post)  # replies cascade via FK
    await db.commit()
    return {"id": post_id, "deleted": True}


async def delete_reply(user_id: UUID, reply_id: UUID, db: AsyncSession) -> dict:
    reply = (
        await db.execute(
            select(GlobeReply).where(
                GlobeReply.id == reply_id,
                GlobeReply.author_user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if reply is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "reply not found"},
        )
    await db.delete(reply)  # nested replies cascade via self-FK
    await db.commit()
    return {"id": reply_id, "deleted": True}


# ─────────────────────── hide / unhide (personal) ────────────────────────

async def hide_zone(user_id: UUID, zone_id: UUID, db: AsyncSession) -> dict:
    exists = (
        await db.execute(select(GlobeZone.id).where(GlobeZone.id == zone_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "zone not found"},
        )
    already = (
        await db.execute(
            select(GlobeHiddenZone.id).where(
                GlobeHiddenZone.user_id == user_id,
                GlobeHiddenZone.zone_id == zone_id,
            )
        )
    ).scalar_one_or_none()
    if already is None:
        db.add(GlobeHiddenZone(user_id=user_id, zone_id=zone_id))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()  # race: already hidden — idempotent
    return {"zone_id": zone_id, "hidden": True}


async def unhide_zone(user_id: UUID, zone_id: UUID, db: AsyncSession) -> dict:
    row = (
        await db.execute(
            select(GlobeHiddenZone).where(
                GlobeHiddenZone.user_id == user_id,
                GlobeHiddenZone.zone_id == zone_id,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        await db.delete(row)
        await db.commit()
    return {"zone_id": zone_id, "hidden": False}


# ─────────────────────── reads (no profile required) ──────────────────────

async def get_zones(
    user_id: UUID, limit: int, offset: int, q: str | None, db: AsyncSession
) -> dict:
    _validate_page(limit, offset)
    hidden = select(GlobeHiddenZone.zone_id).where(
        GlobeHiddenZone.user_id == user_id
    )
    stmt = select(GlobeZone).where(GlobeZone.id.not_in(hidden))
    if q:
        pattern = f"%{_like_escape(q)}%"
        matching = (
            select(GlobeZone.id)
            .outerjoin(GlobePost, GlobePost.zone_id == GlobeZone.id)
            .outerjoin(GlobeReply, GlobeReply.post_id == GlobePost.id)
            .where(
                or_(
                    GlobeZone.title.ilike(pattern, escape="\\"),
                    GlobePost.body.ilike(pattern, escape="\\"),
                    GlobeReply.body.ilike(pattern, escape="\\"),
                )
            )
        )
        stmt = stmt.where(GlobeZone.id.in_(matching))
    rows = (
        await db.execute(
            stmt.order_by(GlobeZone.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()
    return {
        "zones": [
            {
                "id": r.id,
                "domain": r.domain,
                "title": r.title,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    }


async def get_hidden_zones(user_id: UUID, db: AsyncSession) -> dict:
    rows = (
        await db.execute(
            select(GlobeZone)
            .join(GlobeHiddenZone, GlobeHiddenZone.zone_id == GlobeZone.id)
            .where(GlobeHiddenZone.user_id == user_id)
            .order_by(GlobeHiddenZone.created_at.desc())
        )
    ).scalars().all()
    return {
        "zones": [
            {
                "id": r.id,
                "domain": r.domain,
                "title": r.title,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    }


async def get_thread(
    zone_id: UUID, limit: int, offset: int, db: AsyncSession
) -> dict:
    _validate_page(limit, offset)
    zone = (
        await db.execute(select(GlobeZone).where(GlobeZone.id == zone_id))
    ).scalar_one_or_none()
    if zone is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": "zone not found"},
        )
    post_rows = (
        await db.execute(
            select(GlobePost, GlobeProfile.username)
            .join(GlobeProfile, GlobeProfile.user_id == GlobePost.author_user_id)
            .where(GlobePost.zone_id == zone_id)
            .order_by(GlobePost.created_at)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    post_ids = [p.id for p, _ in post_rows]
    replies_by_post: dict = {pid: [] for pid in post_ids}
    if post_ids:
        reply_rows = (
            await db.execute(
                select(GlobeReply, GlobeProfile.username)
                .join(
                    GlobeProfile,
                    GlobeProfile.user_id == GlobeReply.author_user_id,
                )
                .where(GlobeReply.post_id.in_(post_ids))
                .order_by(GlobeReply.created_at)
            )
        ).all()
        for reply, username in reply_rows:
            replies_by_post[reply.post_id].append(
                {
                    "id": reply.id,
                    "parent_reply_id": reply.parent_reply_id,
                    "author_username": username,
                    "body": reply.body,
                    "created_at": reply.created_at,
                }
            )
    return {
        "zone": {
            "id": zone.id,
            "domain": zone.domain,
            "title": zone.title,
            "created_at": zone.created_at,
        },
        "posts": [
            {
                "id": post.id,
                "author_username": username,
                "body": post.body,
                "created_at": post.created_at,
                "replies": replies_by_post[post.id],
            }
            for post, username in post_rows
        ],
    }
