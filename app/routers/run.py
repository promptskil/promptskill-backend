"""POST /run — Engine 2 (Frontier Executor). Subscription-gated + rate-limited.
Runs a user prompt against a chosen frontier model. Independent of /generate."""
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.auth import require_active_subscription
from app.rate_limit import limiter
from app.schemas import RunRequest, RunResponse
from app.services import run_service

router = APIRouter()


@router.post("/run", response_model=RunResponse)
@limiter.limit("20/hour")
async def run(
    request: Request,  # required by slowapi key-func
    response: Response,  # required by slowapi headers_enabled
    body: RunRequest,
    user_id: UUID = Depends(require_active_subscription),
) -> RunResponse:
    result = await run_service.run_prompt(body.model, body.text, user_id)
    return RunResponse(model=result["model"], answer=result["answer"])
