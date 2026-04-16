"""Generate retry task tests — Phase 4 — Step 4.3.

Gate coverage (from /build-checklist):
  - RateLimitError → retries with backoff (4 total invocations)
  - 3 failures → on_failure fires
  - failed_jobs Redis key receives entry
  - Sentry capture called (DSN set) / not called (DSN unset)

Mocking strategy:
  - anthropic.Anthropic client is patched per-test (run() instantiates
    a new one each call — we patch the class constructor).
  - redis.Redis.from_url patched inside _push_failed_job boundary.
  - app.services.generate_service MODEL_REGISTRY is now real (Phase 5
    shipped 2026-04-15) — we load it once at module import below so
    the lazy import inside run() resolves against the real registry.
  - anthropic.RateLimitError can't be cleanly constructed (SDK
    requires response+body). Bypass via __new__ + Exception.__init__.

Autouse mocks (from conftest):
  - _celery_eager runs tasks inline (no worker/broker).
  - mock_resend, _mock_dlq_sinks, _fast_bcrypt — no-ops here.
"""
from unittest.mock import MagicMock, patch

import anthropic
import pytest

# Phase 5 shipped — load the real registry once so the lazy import
# inside generate_task.run() resolves to a populated MODEL_REGISTRY.
from app.services.generate_service import load_model_registry  # noqa: E402

load_model_registry()

from app.tasks import generate_task  # noqa: E402
from app.tasks.generate_task import generate_prompt_task  # noqa: E402


# ─────────────────────── RateLimitError factory ─────────────────────────

def _rate_limit_error(msg: str = "rate limited"):
    """Construct a RateLimitError without invoking SDK constructor.

    The real anthropic.RateLimitError requires an httpx.Response plus
    body payload — more surface than a unit test should pay for. We
    bypass __init__ via __new__ and attach message via Exception.
    isinstance() checks against anthropic.RateLimitError still pass
    because the class hierarchy is preserved.
    """
    err = anthropic.RateLimitError.__new__(anthropic.RateLimitError)
    Exception.__init__(err, msg)
    return err


# ─────────────────────── anthropic client mock helper ───────────────────

@pytest.fixture
def mock_anthropic_success():
    """Patch anthropic.Anthropic so .messages.create returns a text
    response. Mirrors the real SDK shape: response.content[0].text."""
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text="optimized prompt output")]

    client = MagicMock()
    client.messages.create.return_value = fake_response

    with patch(
        "app.tasks.generate_task.anthropic.Anthropic", return_value=client
    ) as ctor:
        yield ctor, client


@pytest.fixture
def mock_anthropic_rate_limited():
    """Anthropic client whose messages.create always raises
    RateLimitError. Used to drive the retry path to exhaustion."""
    client = MagicMock()
    client.messages.create.side_effect = _rate_limit_error()

    with patch(
        "app.tasks.generate_task.anthropic.Anthropic", return_value=client
    ):
        yield client


# ─────────────────────── failed_jobs sink mock ──────────────────────────

@pytest.fixture
def mock_failed_jobs_push():
    """Patch _push_failed_job at the helper boundary — same pattern as
    Phase 4-E _mock_dlq_sinks. Keeps run()/on_failure logic under test
    while isolating real Redis."""
    with patch(
        "app.tasks.generate_task._push_failed_job"
    ) as m:
        yield m


# ─────────────────────── Gate 1: retry on RateLimitError ────────────────

def test_rate_limit_error_retries_three_times(
    mock_anthropic_rate_limited, mock_failed_jobs_push
):
    """Contract: max_retries=3 → 1 initial + 3 retries = 4 total calls.
    Mirrors the email_task retry-contract pattern."""
    generate_prompt_task.delay(
        payload={"model": "claude", "topic": "unit testing"}
    )
    assert mock_anthropic_rate_limited.messages.create.call_count == 4


# ─────────────────────── Gate 2: on_failure → failed_jobs ──────────────

def test_on_failure_pushes_to_failed_jobs(
    mock_anthropic_rate_limited, mock_failed_jobs_push
):
    """After retry exhaustion, failed_jobs sink called once with the
    spec-required entry shape."""
    generate_prompt_task.delay(
        payload={"model": "claude", "topic": "retry coverage"}
    )

    assert mock_failed_jobs_push.call_count == 1
    entry = mock_failed_jobs_push.call_args.args[0]
    assert entry["task_name"] == "generate_prompt_task"
    assert entry["payload"] == {
        "model": "claude", "topic": "retry coverage"
    }
    assert entry["error"]
    assert entry["task_id"]
    assert entry["retry_count"] == 3
    assert entry["timestamp"]  # ISO8601 string


# ─────────────────────── Gate 3: Sentry capture gate ────────────────────

def test_sentry_captures_when_dsn_set(
    mock_anthropic_rate_limited, mock_failed_jobs_push, monkeypatch
):
    """Positive branch — SENTRY_DSN set → capture_exception fires once."""
    monkeypatch.setattr(
        generate_task.settings, "SENTRY_DSN",
        "https://fake@o0.ingest.sentry.io/0",
    )
    with patch.object(
        generate_task.sentry_sdk, "capture_exception"
    ) as cap:
        generate_prompt_task.delay(
            payload={"model": "claude", "topic": "sentry on"}
        )
    assert cap.call_count == 1


def test_sentry_not_called_when_dsn_unset(
    mock_anthropic_rate_limited, mock_failed_jobs_push, monkeypatch
):
    """Negative branch — SENTRY_DSN empty → capture_exception NEVER
    called. Guards against regression if the DSN check is removed and
    unconfigured environments begin emitting events."""
    monkeypatch.setattr(generate_task.settings, "SENTRY_DSN", "")
    with patch.object(
        generate_task.sentry_sdk, "capture_exception"
    ) as cap:
        generate_prompt_task.delay(
            payload={"model": "claude", "topic": "sentry off"}
        )
    assert cap.call_count == 0


# ─────────────────────── Happy path ─────────────────────────────────────

def test_happy_path_returns_response_text(
    mock_anthropic_success, mock_failed_jobs_push
):
    """Successful call → response.content[0].text bubbles up.
    Mock asserts we invoked messages.create exactly once (no retry)."""
    ctor, client = mock_anthropic_success
    result = generate_prompt_task.apply(
        kwargs={"payload": {"model": "claude", "topic": "happy path"}}
    ).get()
    assert result == "optimized prompt output"
    assert client.messages.create.call_count == 1
    # on_failure must NOT have fired
    assert mock_failed_jobs_push.call_count == 0


# ─────────────────────── Celery registration ────────────────────────────

def test_task_registered_on_celery_app():
    """Guards against autodiscover/include drift. Name matches the
    string that generate_service will use in Phase 6 — Step 6.2."""
    from app.tasks.celery_app import celery_app
    assert "generate_prompt_task" in celery_app.tasks
