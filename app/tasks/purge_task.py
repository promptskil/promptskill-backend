"""Weekly purge task — Phase 4 — Step 4.2.

Runs every Monday 00:00 UTC (beat_schedule wired in Phase 4 — Step 4.1b
once this task registers). Deletes expired / spent state across three
tables; Op 4 (orphan cleanup) is a no-op because FK cascade handles
it on user delete (full-stack-engineer L1017-L1018).

Ops executed in order (spec-ordained — implementation.md L984-L1020):
  Op 1: DELETE FROM sessions          WHERE expires_at < NOW()
  Op 2: DELETE FROM password_reset_tokens
                                      WHERE expires_at < NOW()
                                         OR used_at IS NOT NULL
  Op 3: DELETE FROM prompts           WHERE deleted_at IS NOT NULL
                                        AND deleted_at < NOW()
                                                       - INTERVAL '90 days'
  Op 4: orphan cleanup — cascade handles, no SQL

Sentry breadcrumbs: 3 (Ops 1, 2, 3). Always fired, even on zero-row
ops — gives a timestamped audit trail for every weekly run. Breadcrumbs
are cheap and no-op when Sentry isn't initialized.

Engine: reuses app.database_sync.sync_engine (Phase 4-E — Step 4E.3).
Centralized URL normalization (asyncpg → psycopg2) + pool_pre_ping
already handled there — no duplication.

Sentry init: deliberately NOT called here. The
@worker_process_init handler in email_task.py initializes Sentry
once per worker process. add_breadcrumb is safe to call whether init
ran or not — no crash, no cost, just a no-op when DSN unset.
"""
import logging

from sqlalchemy import text

from app.database_sync import sync_engine
from app.tasks.celery_app import celery_app

try:
    import sentry_sdk
except ImportError:  # pragma: no cover — Sentry optional in tests
    sentry_sdk = None

logger = logging.getLogger(__name__)


def _breadcrumb(message: str) -> None:
    """Sentry breadcrumb wrapper — safe when sentry_sdk missing or uninit."""
    if sentry_sdk is None:
        return
    try:
        sentry_sdk.add_breadcrumb(message=message, category="purge")
    except Exception as exc:  # pragma: no cover
        logger.warning("sentry_breadcrumb_failed", extra={"error": str(exc)})


@celery_app.task(name="purge_job_task")
def purge_job_task() -> dict:
    """Run the three DELETE ops in a single transaction.

    Returns a dict with row counts — tested directly, also useful for
    structured logging from Celery Beat on each run.

    Transactional boundary: ONE connection, ONE commit at the end.
    Partial failure → full rollback. Prefer atomicity over op-level
    isolation because all three ops are idempotent retention cleanups;
    there's no business reason to keep a subset applied.
    """
    with sync_engine.connect() as conn:
        # Op 1: expired sessions
        r1 = conn.execute(text(
            "DELETE FROM sessions WHERE expires_at < NOW()"
        ))
        op1_count = r1.rowcount
        _breadcrumb(f"Purge Op1: {op1_count} sessions")

        # Op 2: used or expired reset tokens
        r2 = conn.execute(text(
            "DELETE FROM password_reset_tokens "
            "WHERE expires_at < NOW() OR used_at IS NOT NULL"
        ))
        op2_count = r2.rowcount
        _breadcrumb(f"Purge Op2: {op2_count} reset tokens")

        # Op 3: soft-deleted prompts past 90-day retention
        r3 = conn.execute(text(
            "DELETE FROM prompts "
            "WHERE deleted_at IS NOT NULL "
            "AND deleted_at < NOW() - INTERVAL '90 days'"
        ))
        op3_count = r3.rowcount
        _breadcrumb(f"Purge Op3: {op3_count} prompts")

        # Op 4: orphan cleanup — cascade handles on user delete.
        # No SQL; breadcrumb deliberately omitted per spec
        # ("3 Sentry breadcrumbs (Ops 1, 2, 3 only)").

        conn.commit()

    result = {
        "op1_sessions": op1_count,
        "op2_reset_tokens": op2_count,
        "op3_prompts": op3_count,
    }
    logger.info("purge_job_complete", extra=result)
    return result
