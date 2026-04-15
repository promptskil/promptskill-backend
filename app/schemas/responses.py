from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class TokenResponse(BaseModel):
    token: str
    user_id: UUID


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
