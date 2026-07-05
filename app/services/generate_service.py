"""Model registry + prompt config loader + generate service — Phases 5 & 6.

Phase 5 surface (shipped 2026-04-15):
  MODEL_REGISTRY dict + load_model_registry() — loads on server start.

Phase 6 surface (Layer 7 v2 — multi-provider):
  generate_prompt() — POST /generate orchestration. Four terminating
  paths all converge on a single Phase 1 INSERT into `prompts`:
    1. Provider async success            → version=config['version']
    2. ProviderRateLimitError → Celery   → version=config['version']
    3. ProviderAPIError → fallback       → version='fallback'
    4. asyncio.TimeoutError              → HTTPException 504 (no INSERT)

Spec:
  /full-stack-engineer L401-425, L1476-1586
  /api L417-426 — config shape + 'fallback' reserved
  /data-flow     — Flow 7, 30s ceiling, task.get(timeout=28)
  /path-b-multi-provider-architecture.md — Layer 7 v2
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path

import celery.exceptions
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.prompt import Prompt
from app.services.model_clients import (
    ProviderAPIError,
    ProviderRateLimitError,
    get_client,
)

logger = logging.getLogger(__name__)

_STRIP_RE = re.compile(r'</?[a-z_]+>|\n{3,}')


def _strip_xml_tags(text: str) -> str:
    return _STRIP_RE.sub(lambda m: '' if m.group()[0] == '<' else '\n\n', text).strip()

try:
    import sentry_sdk
except ImportError:  # pragma: no cover
    sentry_sdk = None  # type: ignore[assignment]

MODEL_REGISTRY: dict[str, dict] = {}

_MODELS: tuple[str, ...] = ("claude", "chatgpt", "gemini", "grok")
_REQUIRED_KEYS: tuple[str, ...] = (
    "version",
    "model",
    "provider",
    "provider_model_id",
    "user_message_template",
    "system_prompt",
)
_RESERVED_VERSION = "fallback"

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_model_registry() -> dict[str, dict]:
    """Populate MODEL_REGISTRY from app/prompts/{model}.json files.

    Called on server start via FastAPI startup hook in app/main.py.
    Clears MODEL_REGISTRY before loading so reload-on-start is idempotent.

    Raises:
        FileNotFoundError: if any of the four JSON files is missing.
        ValueError: if a config is missing required keys, if the `model`
            field disagrees with the filename/registry key, or if a
            non-fallback config reuses the reserved 'fallback' version
            string.
        json.JSONDecodeError: if a file is not valid JSON.
    """
    MODEL_REGISTRY.clear()

    for model in _MODELS:
        path = _PROMPTS_DIR / f"{model}.json"
        with path.open(encoding="utf-8") as f:
            config = json.load(f)

        # Shape gate
        missing = [k for k in _REQUIRED_KEYS if k not in config]
        if missing:
            raise ValueError(
                f"{path.name}: missing required keys {missing}"
            )

        # Identity gate — filename, config['model'], registry key must agree
        if config["model"] != model:
            raise ValueError(
                f"{path.name}: model field {config['model']!r} "
                f"does not match filename {model!r}"
            )

        # Reserved-version gate — 'fallback' is never a real version
        if config["version"] == _RESERVED_VERSION:
            raise ValueError(
                f"{path.name}: version {_RESERVED_VERSION!r} is reserved "
                f"for the fallback path and cannot appear in a real config"
            )

        # Provider gate — must be a registered provider in model_clients
        _VALID_PROVIDERS = ("anthropic", "openai", "gemini", "xai")
        if config["provider"] not in _VALID_PROVIDERS:
            raise ValueError(
                f"{path.name}: provider {config['provider']!r} "
                f"not in {_VALID_PROVIDERS}"
            )

        # Template gate — user_message_template must contain {topic}
        if "{topic}" not in config["user_message_template"]:
            raise ValueError(
                f"{path.name}: user_message_template must contain "
                f"'{{topic}}' placeholder"
            )

        MODEL_REGISTRY[model] = config

    return MODEL_REGISTRY


def build_user_message(
    config: dict, topic: str, refinement: str | None = None
) -> str:
    """Compose the model's user message. On refine, connect the original
    topic with the refinement so the model sees both (refine addendum)."""
    base = config["user_message_template"].format(topic=topic)
    if not refinement:
        return base
    return (
        f"{base}\n\n"
        f"The user refined their request: {refinement}\n"
        f"Produce an improved prompt that honors both the original topic "
        f"and this refinement."
    )


# ─────────────────────── Phase 6 — generate_prompt ──────────────────────

_GENERATE_TIMEOUT_SECONDS = 30.0
_CELERY_TIMEOUT_SECONDS = 28  # 2s buffer inside 30s global ceiling
_MAX_TOKENS = 1000


async def generate_prompt(
    model: str,
    topic: str,
    user_id: uuid.UUID,
    app_version: str,
    db: AsyncSession,
    business_id: uuid.UUID | None = None,
    refinement: str | None = None,
) -> dict:
    """Orchestrate a single /generate call.

    Fan-in contract: Anthropic success, Celery success, and fallback
    ALL reach the same Phase 1 INSERT at the bottom. Only asyncio
    timeout and Celery timeout/error raise HTTPException before INSERT.

    Spec: /full-stack-engineer L1478-1583.
    """
    config = MODEL_REGISTRY[model]  # validated by schema Literal

    # Build user message — composes original topic + refinement (Layer 7 v2)
    user_message = build_user_message(config, topic, refinement)

    provider_client = get_client(config["provider"])
    prompt_text: str | None = None

    try:
        prompt_text = await asyncio.wait_for(
            provider_client.agenerate(
                system_prompt=config["system_prompt"],
                user_message=user_message,
                model_id=config["provider_model_id"],
                max_tokens=_MAX_TOKENS,
            ),
            timeout=_GENERATE_TIMEOUT_SECONDS,
        )

    except ProviderRateLimitError:
        # Dispatch to Celery retry worker; API waits on result.
        # Lazy import — breaks circular (generate_task imports MODEL_REGISTRY
        # lazily as well).
        from app.tasks.generate_task import generate_prompt_task

        task = generate_prompt_task.delay({
            "model": model,
            "topic": topic,
            "user_id": str(user_id),
            "refinement": refinement,
        })
        try:
            prompt_text = task.get(timeout=_CELERY_TIMEOUT_SECONDS)
        except celery.exceptions.TimeoutError:
            raise HTTPException(
                status_code=504,
                detail={
                    "error": "timeout",
                    "message": (
                        "Generation timed out. Try again or use a "
                        "shorter topic."
                    ),
                },
            )
        except Exception as exc:
            if sentry_sdk is not None and settings.SENTRY_DSN:
                try:
                    sentry_sdk.capture_exception(exc)
                except Exception:  # pragma: no cover
                    pass
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "server_error",
                    "message": "Generation failed.",
                },
            )

    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail={
                "error": "timeout",
                "message": (
                    "Generation timed out. Try again or use a "
                    "shorter topic."
                ),
            },
        )

    except ProviderAPIError as exc:
        # Degraded path — fallback template. Reserved 'fallback' version
        # written to prompts row for analytics exclusion (spec L818).
        logger.error(
            "provider_api_error_fallback model=%s provider=%s error=%r",
            model,
            config.get("provider"),
            str(exc.original) if hasattr(exc, "original") else str(exc),
        )
        from app.prompts.fallback import get_fallback

        prompt_text = get_fallback(model, topic)
        config = {**config, "version": "fallback"}

    # ─── Phase 1 WriteOperation — single convergence point ──────────
    # Reached from: Anthropic success, Celery success, fallback.
    # NOT reached from: asyncio.TimeoutError, Celery timeout/error.
    if prompt_text is None:
        # Defensive: should be unreachable — all terminating branches
        # either set prompt_text or raise HTTPException.
        raise HTTPException(
            status_code=500,
            detail={"error": "server_error", "message": "Empty response."},
        )

    # Strip XML tags from all paths — provider success, Celery, fallback.
    prompt_text = _strip_xml_tags(prompt_text)

    prompt = Prompt(
        user_id=user_id,
        model=model,
        topic=topic,
        prompt_text=prompt_text,
        system_prompt_version=config["version"],
        app_version=app_version,
        feedback_vote=None,
        business_id=business_id,
    )
    db.add(prompt)
    await db.commit()
    await db.refresh(prompt)

    return {
        "prompt_id": str(prompt.id),
        "prompt": prompt_text,
    }
