"""Generate retry task — Phase 4 — Step 4.3 (Layer 7 v2 — multi-provider).

Class-based Celery task. on_failure is a METHOD on the class
(full-stack-engineer L871-L877) — NOT a nested function inside run(),
which would be unreachable code. Celery calls on_failure automatically
after max_retries is exhausted.

Sync/async boundary (full-stack-engineer L948-L954):
  The Celery worker is a separate PROCESS. FastAPI's async event loop
  does NOT extend into Celery tasks. Do NOT use async/await inside
  task methods. Use ModelClient.generate() (sync), NOT agenerate().

Retry policy (spec L917-L924):
  Only ProviderRateLimitError (HTTP 429) triggers retry.
  countdown = 4 ** retries → 1s, 4s, 16s = 21s total worst case.
  Phase 6 generate service uses task.get(timeout=28) — 2s buffer over
  worst-case retry budget.

on_failure sinks (spec L926-L940):
  1. Redis LPUSH to 'failed_jobs' key — manual weekly review queue
     (full-stack-engineer L1996)
  2. Sentry capture_exception — DSN-gated (see Phase 4-E — Step 4E.3
     for pattern rationale)

acks_late: deliberately NOT set. Differs from send_reset_email_task
because generate has a synchronous caller blocked on task.get(timeout=28);
acks_late + reject_on_worker_lost would redeliver after worker crash,
but by then the HTTP caller has already 500'd.

MODEL_REGISTRY: Phase 5 dependency. Imported lazily inside run() so
this module loads cleanly before Phase 5 ships. Tests mock the import.
"""
import json
import logging
from datetime import datetime, timezone

import redis

from app.config import settings
from app.tasks.celery_app import celery_app

try:
    import sentry_sdk
except ImportError:  # pragma: no cover — Sentry optional in tests
    sentry_sdk = None

logger = logging.getLogger(__name__)

_FAILED_JOBS_KEY = "failed_jobs"


def _push_failed_job(entry: dict) -> None:
    """LPUSH to failed_jobs Redis list. Wrapped in try/finally for
    connection hygiene. Isolated into a helper so tests can patch at
    this boundary without mocking the whole redis module."""
    client = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=2)
    try:
        client.lpush(_FAILED_JOBS_KEY, json.dumps(entry))
    finally:
        try:
            client.close()
        except Exception:  # pragma: no cover
            pass


class GeneratePromptTask(celery_app.Task):
    """Class-based task — on_failure is a method on this class.

    A nested function inside run() would be unreachable (Celery only
    inspects class-level methods named on_failure / on_retry / etc.).
    """

    name = "generate_prompt_task"
    max_retries = 3

    def run(self, payload: dict) -> str:
        model = payload["model"]
        topic = payload["topic"]

        # Lazy import — Phase 5 dependency. Loading this at module
        # top would couple Phase 4 readiness to Phase 5 shipping.
        from app.services.generate_service import MODEL_REGISTRY
        from app.services.model_clients import (
            ProviderRateLimitError,
            get_client,
        )

        config = MODEL_REGISTRY[model]

        # Build user message from config template (Layer 7 v2)
        user_message = config["user_message_template"].format(topic=topic)

        # Sync client per spec sync/async boundary rule.
        provider_client = get_client(config["provider"])

        try:
            return provider_client.generate(
                system_prompt=config["system_prompt"],
                user_message=user_message,
                model_id=config["provider_model_id"],
                max_tokens=1000,
            )
        except ProviderRateLimitError as exc:
            # countdown: 1s, 4s, 16s (4^0, 4^1, 4^2) = 21s worst case.
            raise self.retry(
                exc=exc,
                countdown=4 ** self.request.retries,
            )

    def on_failure(self, exc, task_id, args, kwargs, einfo) -> None:
        """Terminal sink — runs after max_retries exhausted.

        Order matters for observability: push to failed_jobs first
        (durable), then Sentry (alerting). Per-sink try/except so
        one failure does not block the other (G2 invariant from
        Phase 4-E — Step 4E.3).
        """
        # Payload may arrive as positional or keyword argument.
        payload = kwargs.get("payload") if kwargs else None
        if payload is None and args:
            payload = args[0] if args else {}
        payload = payload or {}

        entry = {
            "task_id": task_id,
            "task_name": self.name,
            "payload": payload,
            "error": str(exc),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "retry_count": self.max_retries,
        }

        # Sink 1: Redis failed_jobs LPUSH
        try:
            _push_failed_job(entry)
        except Exception as push_exc:
            logger.error(
                "failed_jobs_push_error",
                extra={"task_id": task_id, "error": str(push_exc)},
            )

        # Sink 2: Sentry capture — DSN-gated
        try:
            if sentry_sdk is not None and settings.SENTRY_DSN:
                sentry_sdk.capture_exception(exc)
        except Exception:  # pragma: no cover
            pass


generate_prompt_task = celery_app.register_task(GeneratePromptTask())
