from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import bearer_scheme
from app.database import get_db
from app.rate_limit import limiter
from app.schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    ResendVerificationRequest,
    ResetPasswordRequest,
    SignupRequest,
    SignupResponse,
    ValidateRequest,
    VerifyEmailCodeRequest,
)
from app.services import auth_service

router = APIRouter()


@router.post("/signup", response_model=SignupResponse)
async def signup(
    body: SignupRequest,
    db: AsyncSession = Depends(get_db),
) -> SignupResponse:
    user_id = await auth_service.signup(body.email, body.password, db)
    return SignupResponse(user_id=user_id)


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    result = await auth_service.login(body.email, body.password, db)
    return LoginResponse(**result)


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


@router.post("/resend-verification")
async def resend_verification(
    body: ResendVerificationRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    # Rule 1: unverified re-entry. Always 200 {success:true} — no
    # enumeration, no verification-state leak. Sends only if the account
    # exists AND is still unverified.
    await auth_service.resend_verification(body.email, db)
    return {"success": True}


@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    await auth_service.reset_password(body.token, body.password, db)
    return {"success": True}


@router.get("/reset-password")
async def reset_password_redirect(token: str) -> RedirectResponse:
    """Email link entry point. Redirects to deep link on mobile (app installed).
    Falls back to a plain HTML message on desktop where promptskill:// cannot open."""
    deep_link = f"promptskill://reset-password?token={token}"
    return RedirectResponse(url=deep_link, status_code=302)


@router.post("/verify-email-code")
@limiter.limit("10/hour")
async def verify_email_code(
    request: Request,
    response: Response,   # REQUIRED by slowapi when headers_enabled=True
    body: VerifyEmailCodeRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    await auth_service.verify_email_code(body.email, body.code, db)
    return {"success": True}
