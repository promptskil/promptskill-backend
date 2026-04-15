"""Generate service tests — Phase 6 — Step 6.2.

Gate coverage (from /build-checklist Step 6.2):
  - Anthropic success → prompt row in DB + app_version + version='v1'
  - Celery success → prompt row + version from config (NOT 'fallback')
  - Fallback (APIStatusError) → prompt row with version='fallback'
  - asyncio.TimeoutError → HTTPException 504, no row
  - Celery TimeoutError → HTTPException 504, no row
  - Celery other exception → HTTPException 500 + Sentry capture

Mocking strategy:
  - Patch anthropic.AsyncAnthropic ctor to return a mock client whose
    .messages.create is an AsyncMock with parameterized behavior.
  - Patch app.tasks.generate_task.generate_prompt_task.delay for the
    Celery fan-out path (test runs in-process — don't invoke eager).
  - Patch app.prompts.fallback.get_fallback for fallback-path isolation
    (real impl covered in test_model_registry.py).
"""
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import anthropic
import celery.exceptions
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select

from app.models.prompt import Prompt
from app.models.user import User
from app.services import generate_service
from app.services.generate_service import generate_prompt, load_model_registry

# Ensure registry is loaded once for this module (same pattern as
# test_generate_task.py).
load_model_registry()


# ─────────────────────── Test helpers ────────────────────────────────────

@pytest_asyncio.fixture(loop_scope="session")
async def test_user(db_session):
    """Insert a user row so prompts.FK(user_id) is satisfied.

    SAVEPOINT rollback guarantees cleanup at teardown.
    """
    user = User(
        email=f"gen-{UUID(int=0).hex[:8]}@test.local",
        password_hash="$2b$04$" + "a" * 53,  # valid bcrypt shape, not used
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def _anthropic_text_response(text: str = "optimized prompt output"):
    """Construct the shape AsyncAnthropic.messages.create returns."""
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    return resp


def _api_status_error():
    """Bypass SDK ctor — APIStatusError needs response+body we don't have."""
    err = anthropic.APIStatusError.__new__(anthropic.APIStatusError)
    Exception.__init__(err, "upstream 500")
    return err


def _rate_limit_error():
    err = anthropic.RateLimitError.__new__(anthropic.RateLimitError)
    Exception.__init__(err, "rate limited")
    return err


# ─────────────────────── Gate 1: Anthropic happy path ───────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_anthropic_success_writes_row(db_session, test_user):
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(
        return_value=_anthropic_text_response("great prompt")
    )
    with patch(
        "app.services.generate_service.anthropic.AsyncAnthropic",
        return_value=fake_client,
    ):
        result = await generate_prompt(
            model="claude",
            topic="machine learning",
            user_id=test_user.id,
            app_version="1.2.3",
            db=db_session,
        )

    assert result["prompt"] == "great prompt"
    assert UUID(result["prompt_id"])

    row = (
        await db_session.execute(
            select(Prompt).where(Prompt.id == UUID(result["prompt_id"]))
        )
    ).scalar_one()
    assert row.user_id == test_user.id
    assert row.model == "claude"
    assert row.topic == "machine learning"
    assert row.prompt_text == "great prompt"
    assert row.system_prompt_version == "v1"
    assert row.app_version == "1.2.3"
    assert row.feedback_vote is None


# ─────────────────────── Gate 2: RateLimit → Celery success ─────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_rate_limit_dispatches_to_celery_and_succeeds(
    db_session, test_user
):
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=_rate_limit_error())

    fake_task = MagicMock()
    fake_task.get.return_value = "celery-recovered output"

    with patch(
        "app.services.generate_service.anthropic.AsyncAnthropic",
        return_value=fake_client,
    ), patch(
        "app.tasks.generate_task.generate_prompt_task.delay",
        return_value=fake_task,
    ) as delay_patch:
        result = await generate_prompt(
            model="chatgpt",
            topic="systems thinking",
            user_id=test_user.id,
            app_version="1.0.0",
            db=db_session,
        )

    assert result["prompt"] == "celery-recovered output"
    delay_patch.assert_called_once()
    args = delay_patch.call_args.args[0]
    assert args["model"] == "chatgpt"
    assert args["topic"] == "systems thinking"
    assert args["user_id"] == str(test_user.id)
    fake_task.get.assert_called_once_with(timeout=28)

    # Row written with non-fallback version from real config
    row = (
        await db_session.execute(
            select(Prompt).where(Prompt.id == UUID(result["prompt_id"]))
        )
    ).scalar_one()
    assert row.system_prompt_version != "fallback"
    assert row.system_prompt_version == "v1"


# ─────────────────────── Gate 3: Celery timeout → 504, no row ───────────

@pytest.mark.asyncio(loop_scope="session")
async def test_celery_timeout_raises_504_no_row(db_session, test_user):
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=_rate_limit_error())

    fake_task = MagicMock()
    fake_task.get.side_effect = celery.exceptions.TimeoutError()

    with patch(
        "app.services.generate_service.anthropic.AsyncAnthropic",
        return_value=fake_client,
    ), patch(
        "app.tasks.generate_task.generate_prompt_task.delay",
        return_value=fake_task,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await generate_prompt(
                model="claude",
                topic="timeout test",
                user_id=test_user.id,
                app_version="1.0.0",
                db=db_session,
            )

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail["error"] == "timeout"

    rows = (
        await db_session.execute(
            select(Prompt).where(
                Prompt.user_id == test_user.id,
                Prompt.topic == "timeout test",
            )
        )
    ).scalars().all()
    assert rows == []


# ─────────────────────── Gate 4: Celery other exc → 500 + Sentry ────────

@pytest.mark.asyncio(loop_scope="session")
async def test_celery_other_exception_500_and_sentry(
    db_session, test_user, monkeypatch
):
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=_rate_limit_error())

    fake_task = MagicMock()
    fake_task.get.side_effect = RuntimeError("broker exploded")

    monkeypatch.setattr(
        generate_service.settings,
        "SENTRY_DSN",
        "https://fake@o0.ingest.sentry.io/0",
    )

    with patch(
        "app.services.generate_service.anthropic.AsyncAnthropic",
        return_value=fake_client,
    ), patch(
        "app.tasks.generate_task.generate_prompt_task.delay",
        return_value=fake_task,
    ), patch.object(
        generate_service.sentry_sdk, "capture_exception"
    ) as cap:
        with pytest.raises(HTTPException) as exc_info:
            await generate_prompt(
                model="grok",
                topic="broker test",
                user_id=test_user.id,
                app_version="1.0.0",
                db=db_session,
            )

    assert exc_info.value.status_code == 500
    assert cap.call_count == 1


# ─────────────────────── Gate 5: asyncio timeout → 504, no row ──────────

@pytest.mark.asyncio(loop_scope="session")
async def test_asyncio_timeout_raises_504_no_row(db_session, test_user):
    """wait_for exceeds 30s — return 504, no DB write."""
    async def _hang(*args, **kwargs):
        import asyncio
        await asyncio.sleep(60)

    fake_client = MagicMock()
    fake_client.messages.create = _hang

    # Shrink the timeout so the test doesn't actually wait 30s
    with patch(
        "app.services.generate_service.anthropic.AsyncAnthropic",
        return_value=fake_client,
    ), patch.object(
        generate_service, "_GENERATE_TIMEOUT_SECONDS", 0.05
    ):
        with pytest.raises(HTTPException) as exc_info:
            await generate_prompt(
                model="claude",
                topic="asyncio timeout",
                user_id=test_user.id,
                app_version="1.0.0",
                db=db_session,
            )

    assert exc_info.value.status_code == 504

    rows = (
        await db_session.execute(
            select(Prompt).where(
                Prompt.user_id == test_user.id,
                Prompt.topic == "asyncio timeout",
            )
        )
    ).scalars().all()
    assert rows == []


# ─────────────────────── Gate 6: APIStatusError → fallback path ─────────

@pytest.mark.asyncio(loop_scope="session")
async def test_api_status_error_writes_fallback_version(
    db_session, test_user
):
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=_api_status_error())

    with patch(
        "app.services.generate_service.anthropic.AsyncAnthropic",
        return_value=fake_client,
    ), patch(
        "app.prompts.fallback.get_fallback",
        return_value="fallback prompt with topic",
    ) as fb:
        result = await generate_prompt(
            model="gemini",
            topic="fallback test",
            user_id=test_user.id,
            app_version="2.0.0",
            db=db_session,
        )

    fb.assert_called_once_with("gemini", "fallback test")
    assert result["prompt"] == "fallback prompt with topic"

    row = (
        await db_session.execute(
            select(Prompt).where(Prompt.id == UUID(result["prompt_id"]))
        )
    ).scalar_one()
    assert row.system_prompt_version == "fallback"
    assert row.prompt_text == "fallback prompt with topic"
    assert row.app_version == "2.0.0"
