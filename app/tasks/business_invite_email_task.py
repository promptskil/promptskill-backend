"""Send-business-invite-email Celery task.

Same reliability pattern as SendResetEmailTask:
  - max_retries=3, countdown=4**retries → 1s, 4s, 16s
  - acks_late + reject_on_worker_lost — no task loss on worker crash
  - Resend Idempotency-Key = Celery task_id — dedupes across retries
  - DLQ on_failure: Sentry → Postgres → Redis → logger (isolated sinks)
  - PII: logs redact recipient, DLQ stores full email for forensics

Sender: noreply@vaineai.com (vaineai.com verified in Resend)
Subject: "You've been invited to join {org_name} on Vaine"
"""
import json
import logging
import sys

import redis
import resend

from app.config import settings
from app.tasks.celery_app import celery_app
from app.tasks.email_task import (
    _dlq_postgres_insert,
    _dlq_redis_push,
    _redact_email,
)

try:
    import sentry_sdk
except ImportError:  # pragma: no cover
    sentry_sdk = None

logger = logging.getLogger(__name__)

resend.api_key = settings.RESEND_API_KEY


class SendBusinessInviteEmailTask(celery_app.Task):
    name = "send_business_invite_email_task"
    max_retries = 3
    acks_late = True
    reject_on_worker_lost = True

    def run(self, email: str, org_name: str, token: str, role: str) -> dict:
        accept_url = (
            f"{settings.APP_BASE_URL}/invite/accept?token={token}"
        )
        result = resend.Emails.send({
            "from": "noreply@vaineai.com",
            "to": email,
            "subject": f"You've been invited to join {org_name} on Vaine",
            "html": (
                f"<p>You've been invited to join <strong>{org_name}</strong> "
                f"on Vaine as a <strong>{role}</strong>.</p>"
                f"<p><a href='{accept_url}'>Accept invitation</a></p>"
                f"<p>This invitation expires in 72 hours. "
                f"If you did not expect this, you can ignore this email.</p>"
            ),
            "headers": {"Idempotency-Key": self.request.id},
        })
        return {"status": "sent", "resend_id": result.get("id")}

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

        # ─── Sink 2: Postgres ───
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

        # ─── Sink 4: structured log ───
        try:
            logger.error(
                "send_business_invite_email_dead_letter",
                extra={
                    "task_id": task_id,
                    "recipient_redacted": _redact_email(email),
                    "error": error_str,
                    "retries_exhausted": self.max_retries,
                },
            )
        except Exception:
            pass


send_business_invite_email_task = celery_app.register_task(
    SendBusinessInviteEmailTask()
)
