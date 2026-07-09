"""Globe router — the subsystem's HTTP surface.

All endpoints gate on get_current_user only (auth + email verification);
never require_active_subscription — Vaine is the single gate, Globe lives
behind Main. Writes require a globe_profiles row (service -> 403
username_required). Reads are open to any verified account.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.rate_limit import limiter
from app.schemas import (
    GlobeMeResponse,
    GlobePostCreateRequest,
    GlobePostOut,
    GlobePostsResponse,
    GlobeReplyCreateRequest,
    GlobeReplyOut,
    GlobeUsernameRequest,
    GlobeUsernameResponse,
    GlobeZoneCreateRequest,
    GlobeZoneOut,
    GlobeZonesResponse,
)
from app.services import globe_service

router = APIRouter()


@router.get("/me", response_model=GlobeMeResponse)
async def me(
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GlobeMeResponse:
    username = await globe_service.get_me(user_id, db)
    return GlobeMeResponse(username=username)


@router.post("/username", response_model=GlobeUsernameResponse)
@limiter.limit("10/hour")
async def claim_username(
    request: Request,
    response: Response,
    body: GlobeUsernameRequest,
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GlobeUsernameResponse:
    username = await globe_service.claim_username(user_id, body.username, db)
    return GlobeUsernameResponse(username=username)


@router.get("/zones", response_model=GlobeZonesResponse)
async def list_zones(
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, max_length=200),
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GlobeZonesResponse:
    result = await globe_service.get_zones(limit, offset, q, db)
    return GlobeZonesResponse(**result)


@router.post("/zones", response_model=GlobeZoneOut)
@limiter.limit("30/hour")
async def create_zone(
    request: Request,
    response: Response,
    body: GlobeZoneCreateRequest,
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GlobeZoneOut:
    result = await globe_service.create_zone(user_id, body.domain, body.title, db)
    return GlobeZoneOut(**result)


@router.get("/zones/{zone_id}/posts", response_model=GlobePostsResponse)
async def list_posts(
    zone_id: UUID,
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GlobePostsResponse:
    result = await globe_service.get_thread(zone_id, limit, offset, db)
    return GlobePostsResponse(**result)


@router.post("/zones/{zone_id}/posts", response_model=GlobePostOut)
@limiter.limit("30/hour")
async def create_post(
    request: Request,
    response: Response,
    zone_id: UUID,
    body: GlobePostCreateRequest,
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GlobePostOut:
    result = await globe_service.create_post(user_id, zone_id, body.body, db)
    return GlobePostOut(**result)


@router.post("/posts/{post_id}/replies", response_model=GlobeReplyOut)
@limiter.limit("30/hour")
async def create_reply(
    request: Request,
    response: Response,
    post_id: UUID,
    body: GlobeReplyCreateRequest,
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GlobeReplyOut:
    result = await globe_service.create_reply(
        user_id, post_id, body.body, body.parent_reply_id, db
    )
    return GlobeReplyOut(**result)
