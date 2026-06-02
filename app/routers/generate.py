"""POST /generate router — Phase 6 — Step 6.3.

Spec:
  /api                   — POST /generate, 60/hour, auth required
  /api addendum 2        — Header(alias="x-app-version") server-side
  /full-stack-engineer   — router wiring

Contract:
  Authorization: Bearer {token}   — required (401 otherwise)
  x-app-version: {string}         — required (400 otherwise)
  body: GenerateRequest           — model Literal, topic 1..500 chars
  rate limit: 60/hour keyed on user_id (via limiter.key_func)

Returns: GenerateResponse {prompt_id, prompt}
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.rate_limit import limiter
from app.schemas import GenerateRequest, GenerateResponse
from app.services import business_service, generate_service

router = APIRouter()


@router.post("/generate", response_model=GenerateResponse)
@limiter.limit("60/hour")
async def generate(
    request: Request,  # required positional for slowapi key-func
    response: Response,  # REQUIRED by slowapi when headers_enabled=True
    body: GenerateRequest,
    x_app_version: str | None = Header(default=None, alias="x-app-version"),
    x_business_id: str | None = Header(default=None, alias="x-business-id"),
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GenerateResponse:
    if not x_app_version:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_header",
                "message": "x-app-version header is required",
            },
        )

    business_id: UUID | None = None
    if x_business_id:
        try:
            business_id = UUID(x_business_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_business_id",
                    "message": "x-business-id must be a valid UUID",
                },
            )
        # Validate membership — never trust a client-supplied org id.
        await business_service.require_member(business_id, user_id, db)

    result = await generate_service.generate_prompt(
        model=body.model,
        topic=body.topic,
        user_id=user_id,
        app_version=x_app_version,
        db=db,
        business_id=business_id,
    )
    return GenerateResponse(
        prompt_id=UUID(result["prompt_id"]),
        prompt=result["prompt"],
    )
