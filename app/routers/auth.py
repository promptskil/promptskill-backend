from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import bearer_scheme, get_current_user
from app.cookies import (
    SESSION_COOKIE_NAME,
    clear_session_cookie,
    is_web_cookie_origin,
    set_session_cookie,
)
from app.database import get_db
from app.models import Session
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
@limiter.limit("5/minute")
async def signup(
    request: Request,
    response: Response,
    body: SignupRequest,
    db: AsyncSession = Depends(get_db),
) -> SignupResponse:
    user_id = await auth_service.signup(body.email, body.password, db)
    return SignupResponse(user_id=user_id)


@router.post("/login", response_model=LoginResponse)
@limiter.limit("5/minute")
async def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    # Browser/extension requests carry an Origin header → short-lived web
    # session (limits localStorage exposure). Native mobile sends no Origin →
    # long-lived session. Page JS in the victim's browser cannot spoof Origin.
    lifetime = (
        auth_service.SESSION_LIFETIME_WEB
        if request.headers.get("origin")
        else auth_service.SESSION_LIFETIME_MOBILE
    )
    result = await auth_service.login(body.email, body.password, db, lifetime)
    # Web origins also receive the token as a host-only HttpOnly cookie
    # (XSS-safe); mobile/extension keep using the body token via bearer.
    if is_web_cookie_origin(request):
        set_session_cookie(
            response,
            result["token"],
            int(lifetime.total_seconds()),
        )
    return LoginResponse(**result)


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> dict:
    # Delete every session the client presented — bearer (mobile/current web)
    # and/or the cookie (web post-migration) — in one statement so no live
    # session is left behind, then always clear the cookie. Idempotent.
    tokens = set()
    if credentials is not None:
        tokens.add(credentials.credentials)
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_token:
        tokens.add(cookie_token)

    if not tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization required",
        )

    await db.execute(delete(Session).where(Session.token.in_(tokens)))
    await db.commit()
    clear_session_cookie(response)
    return {"success": True}


@router.get("/me")
async def me(
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Authed session probe for the web gate (cookie or bearer): 200 with
    {checkout_required} when the session is valid, 401 otherwise. Lets the web
    gate check auth without reading the HttpOnly cookie."""
    return await auth_service.checkout_state(user_id, db)


@router.post("/validate")
async def validate(
    body: ValidateRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    # Always 200 per spec — caller inspects {valid, reason} body
    return await auth_service.validate_token(body.token, db)


@router.post("/forgot-password")
@limiter.limit("1/15 minutes")
async def forgot_password(
    request: Request,
    response: Response,
    body: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    await auth_service.forgot_password(body.email, db)
    return {"success": True}


@router.post("/resend-verification")
@limiter.limit("1/15 minutes")
async def resend_verification(
    request: Request,
    response: Response,
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
