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
import time
import uuid
from pathlib import Path

import celery.exceptions
import httpx
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.prompt import Prompt
from app.services import vaine_engine
from app.services.model_clients import (
    ProviderAPIError,
    ProviderRateLimitError,
    get_client,
)
from app.services.vaine_classifier import classify, threshold_from_taxonomy
from app.services.vaine_format_filter import format_prompt
from app.services.vaine_strengthener import strengthen

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

_VAINE_DIR = Path(__file__).resolve().parent.parent / "vaine_data"

VAINE_LEXICON: dict = {}
VAINE_TAXONOMY: dict = {}
VAINE_RULE_PROFILES: dict[str, dict] = {}


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
        _VALID_PROVIDERS = ("anthropic", "openai", "gemini", "xai", "vaine")
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


def load_vaine_data() -> None:
    """Load Vaine lexicon, taxonomy, and per-model rule profiles on startup.

    Mutates the module dicts in place so importers keep valid references.
    Called on server start via the FastAPI startup hook in app/main.py.

    Raises:
        FileNotFoundError: if a data file is missing.
        ValueError: if an artifact is missing its expected top-level key.
        json.JSONDecodeError: if a file is not valid JSON.
    """
    VAINE_LEXICON.clear()
    VAINE_LEXICON.update(
        json.loads((_VAINE_DIR / "base-lexicon.json").read_text(encoding="utf-8"))
    )
    VAINE_TAXONOMY.clear()
    VAINE_TAXONOMY.update(
        json.loads((_VAINE_DIR / "facet-taxonomy.json").read_text(encoding="utf-8"))
    )
    VAINE_RULE_PROFILES.clear()
    for path in sorted((_VAINE_DIR / "rule-profiles").glob("*.json")):
        VAINE_RULE_PROFILES[path.stem] = json.loads(path.read_text(encoding="utf-8"))

    if "entries" not in VAINE_LEXICON:
        raise ValueError("base-lexicon.json: missing 'entries'")
    if "class_boundary_max_chars" not in VAINE_TAXONOMY:
        raise ValueError("facet-taxonomy.json: missing 'class_boundary_max_chars'")
    if not VAINE_RULE_PROFILES:
        raise ValueError("vaine_data/rule-profiles/: no rule profiles found")
    for name, rp in VAINE_RULE_PROFILES.items():
        for key in ("strength_set", "strip_only", "format_template"):
            if key not in rp:
                raise ValueError(f"rule-profiles/{name}.json: missing {key!r}")


def vaine_health_check(timeout_s: float = 30.0) -> tuple[bool, float]:
    """Ping the Vaine/Together serving host; return (ok, elapsed_seconds).

    Reachability + latency gate for the 30s ceiling. Does not require a
    fine-tuned model — verifies the endpoint is live and responsive.
    """
    url = settings.VAINE_BASE_URL.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {settings.TOGETHER_API_KEY}"}
    start = time.monotonic()
    try:
        resp = httpx.get(url, headers=headers, timeout=timeout_s)
        elapsed = time.monotonic() - start
        return (resp.status_code == 200 and elapsed <= timeout_s, elapsed)
    except httpx.HTTPError:
        return (False, time.monotonic() - start)


async def vaine_generate(model: str, topic: str) -> dict:
    """Run the full Vaine pipeline: classify -> Module rewrite -> strengthen
    -> format. Only agenerate() is generative; the rest is deterministic.
    Returns {"prompt": final_prompt, "class": "simple"|"complex"}.
    """
    profile = VAINE_RULE_PROFILES.get(model)
    if profile is None:
        raise vaine_engine.VaineInferenceError(f"no rule profile for {model!r}")
    cls = classify(topic, threshold_from_taxonomy(VAINE_TAXONOMY))
    raw = await vaine_engine.agenerate(topic)
    strong = strengthen(raw, profile, VAINE_LEXICON)
    return {"prompt": format_prompt(strong, profile), "class": cls}


async def _vaine_path(
    model: str,
    topic: str,
    user_id: uuid.UUID,
    app_version: str,
    business_id: uuid.UUID | None,
    db: AsyncSession,
) -> dict:
    """VAINE_ENABLED path: fine-tuned engine + deterministic tail, then the
    shared INSERT. Failure maps to the degraded 504 posture (D-4).
    """
    try:
        result = await asyncio.wait_for(
            vaine_generate(model, topic), timeout=_GENERATE_TIMEOUT_SECONDS
        )
    except (vaine_engine.VaineInferenceError, asyncio.TimeoutError) as exc:
        logger.error("vaine_generate_failed model=%s error=%r", model, str(exc))
        raise HTTPException(
            status_code=504,
            detail={
                "error": "timeout",
                "message": "Generation timed out. Try again or use a shorter topic.",
            },
        )
    prompt_text = result["prompt"]  # Format Filter owns structure; no strip here
    prompt = Prompt(
        user_id=user_id,
        model=model,
        topic=topic,
        prompt_text=prompt_text,
        system_prompt_version=_VAINE_VERSION,
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
        "metadata": {"engine": "vaine", "class": result["class"]},
    }


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
_VAINE_VERSION = "vaine-v1"  # system_prompt_version tag for the Vaine path


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

    # Vaine path (flag-gated, dual-run). Legacy provider path below is untouched.
    if settings.VAINE_ENABLED:
        return await _vaine_path(
            model, topic, user_id, app_version, business_id, db
        )

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
