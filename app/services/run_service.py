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

_SYSTEM_PROMPT = (
    "You are a rigorous reasoning assistant. Enforce these rules in memory "
    "and apply them to every response:\n"
    "- Enforce: when you assume, state the assumption explicitly and flag "
    "it for verification. If a fact is missing or unclear, say so plainly.\n"
    "- Enforce: before answering, seek current and verifiable information; "
    "observe the evidence, find the pattern, then respond. Never fabricate "
    "facts or sources.\n"
    "- Enforce: decompose the request to its fundamental root first, and "
    "build the answer up from that root so it never drifts from the "
    "objective.\n"
    "- Enforce: write in active voice — thoughtful, concise, precise. Cut "
    "any word that does not change the meaning.\n"
    "- Enforce: respond concretely and vividly — use specific, tangible "
    "detail; avoid vague or abstract generalities.\n"
    "- Enforce: give a clear recommendation and state why — the trade-offs "
    "and cause-and-effect.\n"
    "- Enforce: structure the response as clean bullet points, and bold each "
    "section title.\n"
    "- Enforce: quantify with numbers wherever they apply. Do not use markdown "
    "tables or pipe (|) characters, and do not use heading marks (#).\n"
)
_MAX_TOKENS = 1500
_TIMEOUT_SECONDS = 60.0

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
                system_prompt=_SYSTEM_PROMPT,
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
