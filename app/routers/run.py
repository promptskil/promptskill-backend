"""POST /run — Engine 2 (Frontier Executor). Subscription-gated + rate-limited.
Runs a user prompt against a chosen frontier model. Independent of /generate.

/run        — JSON (full answer). Kept for in-field mobile builds.
/run/stream — SSE (token deltas) for low perceived latency; web + new mobile.

Both routes share ONE 20/hour quota (scope="run") so the two transports of the
same expensive operation can't be summed to double the per-user cap.
"""
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from app.auth import require_active_subscription
from app.rate_limit import limiter
from app.schemas import RunRequest, RunResponse
from app.services import run_service

router = APIRouter()
logger = logging.getLogger(__name__)

# Shared quota: /run (JSON) and /run/stream (SSE) count against one bucket.
run_limit = limiter.shared_limit("20/hour", scope="run")


@router.post("/run", response_model=RunResponse)
@run_limit
async def run(
    request: Request,  # required by slowapi key-func
    response: Response,  # required by slowapi headers_enabled
    body: RunRequest,
    user_id: UUID = Depends(require_active_subscription),
) -> RunResponse:
    result = await run_service.run_prompt(body.model, body.text, user_id)
    return RunResponse(model=result["model"], answer=result["answer"])


@router.post("/run/stream")
@run_limit
async def run_stream(
    request: Request,  # required by slowapi key-func
    response: Response,  # required by slowapi headers_enabled
    body: RunRequest,
    user_id: UUID = Depends(require_active_subscription),
) -> StreamingResponse:
    # Pre-flight — all real HTTP status BEFORE the SSE stream opens:
    #   auth/subscription (require_active_subscription → 401/402),
    #   body validation (RequestValidationError → global handler → 400),
    #   shared route limit (slowapi → 429), daily cap (run_daily_preflight → 429).
    cap, key = await run_service.run_daily_preflight(body.model, user_id)

    async def _sse():
        # Once the first byte is sent the HTTP status is committed, so any
        # provider error mid-stream is delivered as an SSE `event: error`,
        # never an HTTP status. json.dumps escapes newlines so each delta is
        # exactly one `data:` frame.
        try:
            async for chunk in run_service.stream_prompt(
                body.model, body.text, cap, key
            ):
                yield f"data: {json.dumps(chunk)}\n\n"
            yield "event: done\ndata: {}\n\n"
        except HTTPException as exc:
            yield f"event: error\ndata: {json.dumps(exc.detail)}\n\n"
        # Last-resort: an unmapped error still reaches the client as an error
        # frame, never a silent truncated stream. CancelledError is a
        # BaseException, so client-disconnect cancellation still propagates.
        except Exception:  # noqa: BLE001 — signal, don't truncate silently
            logger.exception("run_stream_unexpected model=%s", body.model)
            detail = {
                "error": "provider_error",
                "message": "The model could not complete the request.",
            }
            yield f"event: error\ndata: {json.dumps(detail)}\n\n"

    return StreamingResponse(
        _sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
