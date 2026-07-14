from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class VerifyEmailCodeRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=128)


class ValidateRequest(BaseModel):
    token: str


class GenerateRequest(BaseModel):
    model: Literal["claude", "chatgpt", "gemini", "grok"]
    topic: str = Field(min_length=1, max_length=500)
    refinement: str | None = Field(default=None, max_length=500)


class RunRequest(BaseModel):
    model: Literal["chatgpt", "claude-sonnet", "gemini", "grok"]
    text: str = Field(min_length=1, max_length=8000)


class FeedbackRequest(BaseModel):
    prompt_id: UUID
    vote: Literal["up", "down"]


class UpdateEmailRequest(BaseModel):
    email: EmailStr


# ─────────────────────── Globe subsystem ─────────────────────────────────

class GlobeUsernameRequest(BaseModel):
    username: str = Field(
        min_length=3, max_length=20, pattern=r"^[A-Za-z0-9_]+$"
    )


class GlobeZoneCreateRequest(BaseModel):
    domain: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=120)


class GlobePostCreateRequest(BaseModel):
    body: str = Field(min_length=1)


class GlobeReplyCreateRequest(BaseModel):
    body: str = Field(min_length=1)
    parent_reply_id: UUID | None = None


class GlobeEditRequest(BaseModel):
    body: str = Field(min_length=1)
