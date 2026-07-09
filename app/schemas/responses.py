from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr


class TokenResponse(BaseModel):
    token: str
    user_id: UUID


class SignupResponse(BaseModel):
    user_id: UUID
    email_verification_required: bool = True


class LoginResponse(BaseModel):
    token: str
    user_id: UUID
    account_type: str  # individual | admin | employee
    business_id: UUID | None
    checkout_required: bool = False


class GenerateResponse(BaseModel):
    prompt_id: UUID
    prompt: str
    metadata: dict | None = None  # Vaine path: {engine, class}; legacy: None


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


# ─────────────────────── Business layer ──────────────────────────────────

class BusinessResponse(BaseModel):
    id: UUID
    name: str
    owner_id: UUID
    seat_limit: int
    created_at: datetime


class BusinessMemberResponse(BaseModel):
    user_id: UUID
    email: EmailStr
    role: str
    joined_at: datetime


class BusinessMineResponse(BaseModel):
    id: UUID
    name: str
    owner_id: UUID
    seat_limit: int
    members: list[BusinessMemberResponse]
    created_at: datetime


class BusinessInviteResponse(BaseModel):
    id: UUID
    email: str
    role: str
    expires_at: datetime
    created_at: datetime


class BusinessHistoryItem(BaseModel):
    prompt_id: UUID
    user_id: UUID
    model: str
    topic: str
    prompt_text: str
    system_prompt_version: str
    app_version: str
    feedback_vote: str | None
    created_at: datetime


class BusinessHistoryResponse(BaseModel):
    items: list[BusinessHistoryItem]
    total: int
    limit: int
    offset: int


class RemoveMemberResponse(BaseModel):
    user_id: UUID
    removed: bool


class AcceptInviteResponse(BaseModel):
    token: str
    user_id: UUID
    account_type: str  # always "employee"
    business_id: UUID


# ─────────────────────── Globe subsystem ─────────────────────────────────

class GlobeMeResponse(BaseModel):
    username: str | None


class GlobeUsernameResponse(BaseModel):
    username: str


class GlobeZoneOut(BaseModel):
    id: UUID
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
