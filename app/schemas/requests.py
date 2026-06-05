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


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=128)


class ValidateRequest(BaseModel):
    token: str


class GenerateRequest(BaseModel):
    model: Literal["claude", "chatgpt", "gemini", "grok"]
    topic: str = Field(min_length=1, max_length=500)
    refinement: str | None = Field(default=None, max_length=500)


class FeedbackRequest(BaseModel):
    prompt_id: UUID
    vote: Literal["up", "down"]


class UpdateEmailRequest(BaseModel):
    email: EmailStr


# ─────────────────────── Business layer ──────────────────────────────────

class CreateBusinessRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class InviteBusinessMemberRequest(BaseModel):
    email: EmailStr
    role: Literal["employee"] = "employee"


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=1)
    password: str = Field(min_length=8, max_length=128)
