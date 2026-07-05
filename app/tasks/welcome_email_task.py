"""Send-welcome-email Celery task.

Dispatched post-commit from auth_service.signup().
Follows the exact pattern of SendResetEmailTask (email_task.py):
  - acks_late + reject_on_worker_lost — no task loss on worker crash.
  - Resend Idempotency-Key = Celery task_id — dedupes across retries.
  - Retry: max_retries=3, countdown=4**retries → 1s, 4s, 16s.
  - DLQ on_failure: Sentry → Postgres → Redis → Logger (isolated sinks).
  - PII: logs redact recipient. DLQ stores full email for audit.

Sync rule: worker is a separate process. No async/await.
"""
import json
import logging
import sys

import redis
import resend
from celery.signals import worker_process_init

try:
    import sentry_sdk
except ImportError:  # pragma: no cover
    sentry_sdk = None

from app.config import settings
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

resend.api_key = settings.RESEND_API_KEY

_DLQ_LIST_KEY = "dlq:emails"
_DLQ_LIST_MAX = 1000


@worker_process_init.connect
def _init_sentry_welcome(**_kwargs) -> None:
    if not (settings.SENTRY_DSN and sentry_sdk is not None):
        return
    integrations = []
    try:
        from sentry_sdk.integrations.celery import CeleryIntegration
        integrations.append(CeleryIntegration())
    except Exception:  # noqa: BLE001
        pass
    try:
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        integrations.append(SqlalchemyIntegration())
    except Exception:  # noqa: BLE001
        pass

    init_kwargs = {
        "dsn": settings.SENTRY_DSN,
        "environment": settings.SENTRY_ENVIRONMENT,
        "traces_sample_rate": settings.SENTRY_TRACES_SAMPLE_RATE,
        "profiles_sample_rate": settings.SENTRY_PROFILES_SAMPLE_RATE,
        "integrations": integrations,
        "send_default_pii": False,
    }
    if settings.SENTRY_RELEASE:
        init_kwargs["release"] = settings.SENTRY_RELEASE

    try:
        sentry_sdk.init(**init_kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("sentry_init_failed_disabling", extra={"error": str(exc)})


# ─────────────────────────── helpers ────────────────────────────

def _redact_email(email: str) -> str:
    if not email or "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def _dlq_postgres_insert(  # pragma: no cover
    task_id: str,
    task_name: str,
    recipient: str,
    error: str,
    retries_exhausted: int,
) -> None:
    from app.database_sync import SessionLocal
    from app.models import DeadLetterEmail

    with SessionLocal() as db:
        db.add(
            DeadLetterEmail(
                task_id=task_id,
                task_name=task_name,
                recipient=recipient,
                error=error,
                retries_exhausted=retries_exhausted,
            )
        )
        db.commit()


def _dlq_redis_push(payload: dict) -> None:  # pragma: no cover
    client = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=2)
    try:
        pipe = client.pipeline()
        pipe.lpush(_DLQ_LIST_KEY, json.dumps(payload))
        pipe.ltrim(_DLQ_LIST_KEY, 0, _DLQ_LIST_MAX - 1)
        pipe.execute()
    finally:
        try:
            client.close()
        except Exception:  # nosec
            pass


# ─────────────────────────── task ───────────────────────────────

class SendWelcomeEmailTask(celery_app.Task):
    name = "send_welcome_email_task"
    max_retries = 3
    acks_late = True
    reject_on_worker_lost = True

    def run(self, email: str) -> dict:
        try:
            result = resend.Emails.send({
                "from": "noreply@vaineai.com",
                "to": email,
                "subject": "Welcome to Vaine",
                "html": (
                    "<p>Your account is ready.</p>"
                    "<p>Pick a model, enter a topic, and get a crafted "
                    "prompt in seconds.</p>"
                ),
                "headers": {"Idempotency-Key": self.request.id},
            })
            return {"status": "sent", "resend_id": result.get("id")}

        except resend.exceptions.ResendError as exc:
            logger.warning(
                "send_welcome_email_retry",
                extra={
                    "recipient_redacted": _redact_email(email),
                    "attempt": self.request.retries + 1,
                    "error": str(exc),
                },
            )
            raise self.retry(
                exc=exc,
                countdown=4 ** self.request.retries,
            )

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Terminus. Four sinks, isolated. No cascading failures."""
        email = args[0] if args else ""
        error_str = str(exc)

        # ─── Sink 1: Sentry ───
        try:
            if sentry_sdk is not None and settings.SENTRY_DSN:
                sentry_sdk.capture_exception(exc)
        except Exception:  # nosec
            pass

        # ─── Sink 2: Postgres (source of truth) ───
        try:
            _dlq_postgres_insert(
                task_id=task_id,
                task_name=self.name,
                recipient=email,
                error=error_str,
                retries_exhausted=self.max_retries,
            )
        except Exception as db_exc:  # noqa: BLE001
            try:
                print(
                    f"DLQ_POSTGRES_FAIL task_id={task_id} "
                    f"recipient={_redact_email(email)} err={db_exc}",
                    file=sys.stderr,
                    flush=True,
                )
            except Exception:
                pass

        # ─── Sink 3: Redis hot-view ───
        try:
            _dlq_redis_push({
                "task_id": task_id,
                "recipient_redacted": _redact_email(email),
                "error": error_str,
            })
        except Exception as redis_exc:  # noqa: BLE001
            try:
                print(
                    f"DLQ_REDIS_FAIL task_id={task_id} err={redis_exc}",
                    file=sys.stderr,
                    flush=True,
                )
            except Exception:
                pass

        # ─── Sink 4: structured log (final) ───
        try:
            logger.error(
                "send_welcome_email_dead_letter",
                extra={
                    "task_id": task_id,
                    "recipient_redacted": _redact_email(email),
                    "error": error_str,
                    "retries_exhausted": self.max_retries,
                },
            )
        except Exception:
            pass


send_welcome_email_task = celery_app.register_task(SendWelcomeEmailTask())
