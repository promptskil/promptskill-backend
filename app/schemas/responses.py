from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr


class TokenResponse(BaseModel):
    token: str
    user_id: UUID


class LoginResponse(BaseModel):
    token: str
    user_id: UUID
    account_type: str  # individual | admin | employee
    business_id: UUID | None


class GenerateResponse(BaseModel):
    prompt_id: UUID
    prompt: str


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
    business_id: UUID
    role: str
