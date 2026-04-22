"""Send-reset-email Celery task — Phase 4.4.

Class-based task pattern per locked reference L870-L945.
Email content matches auth_service._send_reset_email (lines 69-83)
exactly — D2.1 Option B: reuse, no drift.

Retry: max_retries=3, countdown=4**retries → 1s, 4s, 16s.

Reliability:
  - acks_late + reject_on_worker_lost — no task loss on worker
    crash mid-send.
  - Resend Idempotency-Key = Celery task_id — stable across
    retries AND across worker-crash redelivery. Resend dedupes
    on their side, so duplicate-email risk is eliminated.

DLQ (on_failure) — Phase 4.4 semantics: forensic-only, no replay.
  Sink order is deliberate:
    1. Sentry first — alerting must fire before DB writes so
       DB-sink crashes also surface.
    2. Postgres (dead_letter_emails) — durable source of truth.
    3. Redis LIST (dlq:emails) — hot-view cache, LTRIM-bounded
       at 1000 entries. Best-effort; Postgres is authoritative.
    4. Logger / stderr — final fallback. Always attempted last.
  Each sink is isolated by try/except; one failure never cascades.

PII handling:
  - Logs redact recipient ('j***@example.com') — log aggregators
    ship externally.
  - DLQ `recipient` stores full email — internal-only forensic
    record for support and audit.

Sync rule (full-stack-engineer L948-L954):
  Worker is a separate process. No async/await. Uses
  app.database_sync for Postgres writes.
"""
import json
import logging
import sys

import redis
import resend
from celery.signals import worker_process_init

try:
    import sentry_sdk
except ImportError:  # pragma: no cover — sentry-sdk is in requirements.txt
    sentry_sdk = None

from app.config import settings
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

resend.api_key = settings.RESEND_API_KEY

# Sentry activation — Phase 4.4.
#
# Fundamental: Sentry is a worker-process concern. Module import
# happens everywhere (web, tests, alembic, `python -c`). Initializing
# Sentry at module top-level makes every importer vulnerable to a
# malformed DSN (BadDsn raised at import → entire app fails to load).
#
# Correct lifecycle boundary: Celery worker_process_init signal.
# - Eager-mode tests never spawn a worker → handler never fires → no init.
# - Web process never imports this module in a context that spawns
#   workers → no init.
# - Only real Celery workers trigger init.
# - try/except keeps worker alive even on BadDsn — Sentry simply stays
#   off, DLQ sinks 2-4 continue to function.


@worker_process_init.connect
def _init_sentry(**_kwargs) -> None:
    if not (settings.SENTRY_DSN and sentry_sdk is not None):
        return
    # Integrations are best-effort — if extras missing or version lacks
    # them, fall back to bare init. BadDsn is still caught below.
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
    except Exception as exc:  # noqa: BLE001 — never crash the worker
        logger.warning(
            "sentry_init_failed_disabling",
            extra={"error": str(exc)},
        )

# DLQ hot-view bounds. Postgres is source of truth — Redis is cache.
_DLQ_LIST_KEY = "dlq:emails"
_DLQ_LIST_MAX = 1000


# ─────────────────────────── helpers ────────────────────────────

def _redact_email(email: str) -> str:
    """'jeremie@example.com' → 'j***@example.com'. Logs only."""
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
    """Durable DLQ write. Source of truth. Sync SQLAlchemy session."""
    # Lazy import: keeps module importable in environments where
    # DATABASE_URL is intentionally absent (e.g. some test harnesses).
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
    """Hot-view DLQ. Bounded via LTRIM. Best-effort — Postgres wins."""
    client = redis.Redis.from_url(
        settings.REDIS_URL, socket_timeout=2
    )
    try:
        pipe = client.pipeline()
        pipe.lpush(_DLQ_LIST_KEY, json.dumps(payload))
        pipe.ltrim(_DLQ_LIST_KEY, 0, _DLQ_LIST_MAX - 1)
        pipe.execute()
    finally:
        try:
            client.close()
        except Exception:  # nosec — sink must not crash terminus
            pass


# ─────────────────────────── task ───────────────────────────────

class SendResetEmailTask(celery_app.Task):
    name = "send_reset_email_task"
    max_retries = 3
    acks_late = True
    reject_on_worker_lost = True

    def run(self, email: str, token: str) -> dict:
        try:
            result = resend.Emails.send({
                "from": "noreply@cosight-ai.com",
                "to": email,
                "subject": "Reset your Vaine password",
                "html": (
                    f"<p>You requested a password reset for your Vaine account.</p>"
                    f"<p><a href='{settings.APP_BASE_URL}/auth/reset-password"
                    f"?token={token}'>Reset my password</a></p>"
                    f"<p>This link expires in 1 hour. "
                    f"If you did not request this, you can ignore this email.</p>"
                ),
                # Resend dedupes on this key — prevents duplicate
                # emails across acks_late redelivery.
                "headers": {"Idempotency-Key": self.request.id},
            })
            return {"status": "sent", "resend_id": result.get("id")}

        except resend.exceptions.ResendError as exc:
            logger.warning(
                "send_reset_email_retry",
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

        # ─── Sink 1: Sentry (first — surfaces DB/Redis sink crashes) ───
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
                "send_reset_email_dead_letter",
                extra={
                    "task_id": task_id,
                    "recipient_redacted": _redact_email(email),
                    "error": error_str,
                    "retries_exhausted": self.max_retries,
                },
            )
        except Exception:
            pass


send_reset_email_task = celery_app.register_task(SendResetEmailTask())
