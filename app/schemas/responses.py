from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class TokenResponse(BaseModel):
    token: str
    user_id: UUID


class SignupResponse(BaseModel):
    user_id: UUID
    email_verification_required: bool = True


class LoginResponse(BaseModel):
    token: str
    user_id: UUID
    checkout_required: bool = False


class GenerateResponse(BaseModel):
    prompt_id: UUID
    prompt: str
    metadata: dict | None = None  # Vaine path: {engine, class}; legacy: None


class RunResponse(BaseModel):
    model: str
    answer: str


class CheckoutResponse(BaseModel):
    url: str


# ─────────────────────── Phase 7 — Feedback & History ────────────────────

class FeedbackResponse(BaseModel):
    prompt_id: UUID
    vote: str  # "up" | "down"


class HistoryItem(BaseModel):
    prompt_id: UUID
    model: str
    topic: str
    prompt_text: str
    system_prompt_version: str
    app_version: str
    feedback_vote: str | None
    created_at: datetime


class HistoryResponse(BaseModel):
    items: list[HistoryItem]
    total: int
    limit: int
    offset: int


class DeleteResponse(BaseModel):
    prompt_id: UUID
    deleted_at: datetime


# ─────────────────────── Phase 8 — User profile ──────────────────────────

class UserResponse(BaseModel):
    id: UUID
    email: str


# ─────────────────────── Globe subsystem ─────────────────────────────────

class GlobeMeResponse(BaseModel):
    username: str | None


class GlobeUsernameResponse(BaseModel):
    username: str


class GlobeZoneOut(BaseModel):
    id: UUID
    domain: str
    title: str
    created_at: datetime


class GlobeZonesResponse(BaseModel):
    zones: list[GlobeZoneOut]


class GlobeReplyOut(BaseModel):
    id: UUID
    parent_reply_id: UUID | None
    author_username: str
    body: str
    created_at: datetime


class GlobePostOut(BaseModel):
    id: UUID
    author_username: str
    body: str
    created_at: datetime
    replies: list[GlobeReplyOut]


class GlobePostsResponse(BaseModel):
    zone: GlobeZoneOut
    posts: list[GlobePostOut]


class GlobeEditedResponse(BaseModel):
    id: UUID
    body: str


class GlobeHideResponse(BaseModel):
    zone_id: UUID
    hidden: bool


class GlobeDeletedResponse(BaseModel):
    id: UUID
    deleted: bool


class GlobeDomainsResponse(BaseModel):
    domains: list[str]


class GlobeFeedItem(BaseModel):
    post_id: UUID
    zone_id: UUID
    zone_title: str
    zone_domain: str
    author_username: str
    body: str
    created_at: datetime


class GlobeFeedResponse(BaseModel):
    items: list[GlobeFeedItem]
    next_cursor: str | None = None
