from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import bearer_scheme
from app.database import get_db
from app.schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    ResetPasswordRequest,
    SignupRequest,
    TokenResponse,
    ValidateRequest,
)
from app.services import auth_service

router = APIRouter()


@router.post("/signup", response_model=TokenResponse)
async def signup(
    body: SignupRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    token, user_id = await auth_service.signup(body.email, body.password, db)
    return TokenResponse(token=token, user_id=user_id)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    token, user_id = await auth_service.login(body.email, body.password, db)
    return TokenResponse(token=token, user_id=user_id)


@router.post("/logout")
async def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> dict:
    # Idempotent: missing/invalid bearer still returns success.
    # DELETE WHERE token=? is a no-op if token doesn't exist.
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required",
        )
    await auth_service.logout(credentials.credentials, db)
    return {"success": True}


@router.post("/validate")
async def validate(
    body: ValidateRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    # Always 200 per spec — caller inspects {valid, reason} body
    return await auth_service.validate_token(body.token, db)


@router.post("/forgot-password")
async def forgot_password(
    body: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    await auth_service.forgot_password(body.email, db)
    return {"success": True}


@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    await auth_service.reset_password(body.token, body.password, db)
    return {"success": True}
