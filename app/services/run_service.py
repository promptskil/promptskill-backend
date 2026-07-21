"""Engine 2 — Frontier Executor. Runs a user-supplied prompt against a chosen
frontier model via the provider adapters. Independent of the Vaine (/generate)
path. Expensive models get a per-user daily cap via Redis."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import HTTPException

from app.config import settings
from app.services.model_clients import (
    ProviderAPIError,
    ProviderRateLimitError,
    get_client,
)

logger = logging.getLogger(__name__)

# dropdown key -> (provider, model_id). Slugs verified against live model lists.
MODEL_MAP: dict[str, tuple[str, str]] = {
    "chatgpt": ("openai", "gpt-5.5"),
    "claude-sonnet": ("anthropic", "claude-sonnet-5"),
    "gemini": ("gemini", "gemini-3.5-flash"),
    "grok": ("xai", "grok-4.5"),
}

# Per-user daily caps (UTC day) on expensive models. Empty now — none capped.
DAILY_CAPS: dict[str, int] = {}

_MAX_TOKENS = 1500
# Backend deadline sits UNDER the frontend /run timeout (60s in api.ts) so the
# backend always returns its controlled 502 before the browser aborts — no
# client-side 504/502 race on a provider hang.
_TIMEOUT_SECONDS = 55.0

_redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)


def _daily_key(user_id: UUID, model: str) -> str:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"run:daily:{user_id}:{model}:{day}"


async def run_prompt(model: str, text: str, user_id: UUID) -> dict:
    """Run `text` against `model` (a MODEL_MAP key). Enforces the per-user
    daily cap for capped models, counting only successful calls."""
    provider, model_id = MODEL_MAP[model]  # model validated by RunRequest Literal

    cap = DAILY_CAPS.get(model)
    key = _daily_key(user_id, model) if cap is not None else None
    if cap is not None:
        used = await _redis.get(key)
        if used is not None and int(used) >= cap:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "daily_limit",
                    "message": (
                        f"Daily limit reached for this model ({cap}/day). "
                        f"Try again tomorrow."
                    ),
                },
            )

    client = get_client(provider)
    try:
        answer = await asyncio.wait_for(
            client.agenerate(
                # Engine 2 is a pure passthrough: `text` is already a finished
                # Vaine prompt, so no system layer is added — the adapters omit
                # the system message and the model returns its natural output.
                system_prompt="",
                user_message=text,
                model_id=model_id,
                max_tokens=_MAX_TOKENS,
            ),
            timeout=_TIMEOUT_SECONDS,
        )
    except ProviderRateLimitError:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limited",
                "message": "The model is busy. Try again shortly.",
            },
        )
    except (ProviderAPIError, asyncio.TimeoutError) as exc:
        logger.error(
            "run_failed model=%s provider=%s error=%r", model, provider, str(exc)
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error": "provider_error",
                "message": "The model could not complete the request.",
            },
        )

    # Count only successful calls against the daily cap.
    if cap is not None and key is not None:
        new = await _redis.incr(key)
        if new == 1:
            await _redis.expire(key, 86400)

    return {"model": model, "answer": answer}
