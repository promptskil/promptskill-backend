"""Phase 4.5 — email_task behavior tests.

Covers:
  - retry mechanics (1 initial + 3 retries = 4 Resend invocations)
  - Idempotency-Key header contract with Resend (duplicate prevention)
  - DLQ Postgres + Redis sinks fire on retry exhaustion
  - Sentry gate — called only when SENTRY_DSN non-empty (both branches)
  - Sink isolation — Postgres failure does not block Redis sink
  - PII redaction in retry log records
  - _redact_email helper unit tests

Strategy:
  - Celery eager mode (session autouse from conftest) runs tasks inline.
  - mock_resend (autouse) — default success; individual tests set
    .side_effect = ResendError to force retry path.
  - _mock_dlq_sinks (autouse) — wraps both sink functions; tests
    that care about sink behavior pull the yielded (pg, rd) tuple
    and assert on it.

No db_session — these tests exercise the task boundary, not the
service. auth_service-level tests live in test_auth_service.py.
"""
import logging
from unittest.mock import patch

import pytest
import resend

from app.tasks import email_task
from app.tasks.email_task import _redact_email, send_reset_email_task


def _resend_error():
    """Fresh ResendError — constructor-keyed to current SDK signature."""
    return resend.exceptions.ResendError(
        code=500,
        message="upstream failure",
        suggested_action="retry",
        error_type="server_error",
    )


# ─────────────────────── retry mechanics ────────────────────────

def test_send_reset_email_task_retries_3_times(mock_resend):
    """Contract: max_retries=3 → 1 initial + 3 retries = 4 total sends.

    Catches regression where max_retries is changed without matching
    countdown bound adjustment.
    """
    mock_resend.side_effect = _resend_error()
    send_reset_email_task.delay("x@test.com", "tok")
    assert mock_resend.call_count == 4


# ─────────────────────── idempotency contract ───────────────────

def test_send_reset_email_includes_idempotency_key(mock_resend):
    """Resend must receive Idempotency-Key header on every send.

    Contract test for duplicate-email prevention across acks_late
    redelivery (worker crash mid-send). This test asserts we SEND
    the header — it does NOT assert Resend honors it (dashboard
    verification required).
    """
    send_reset_email_task.delay("x@test.com", "tok")
    call_payload = mock_resend.call_args[0][0]
    assert "headers" in call_payload, "send payload missing headers dict"
    assert "Idempotency-Key" in call_payload["headers"], \
        "Idempotency-Key header not passed to Resend"
    assert len(call_payload["headers"]["Idempotency-Key"]) > 0


# ─────────────────────── DLQ sinks on exhaustion ────────────────

def test_dlq_postgres_sink_receives_row_on_exhaustion(
    mock_resend, _mock_dlq_sinks
):
    """After retry exhaustion, Postgres sink called exactly once with
    task metadata. Source-of-truth DLQ contract."""
    pg, _rd = _mock_dlq_sinks
    mock_resend.side_effect = _resend_error()
    send_reset_email_task.delay("dlq-pg@test.com", "tok")

    assert pg.call_count == 1
    kwargs = pg.call_args.kwargs
    assert kwargs["task_name"] == "send_reset_email_task"
    assert kwargs["recipient"] == "dlq-pg@test.com"
    assert kwargs["retries_exhausted"] == 3
    assert kwargs["error"]  # non-empty
    assert kwargs["task_id"]  # non-empty


def test_dlq_redis_sink_receives_payload_on_exhaustion(
    mock_resend, _mock_dlq_sinks
):
    """After retry exhaustion, Redis sink called exactly once with
    redacted payload. Hot-view DLQ contract — PII redaction confirmed."""
    _pg, rd = _mock_dlq_sinks
    mock_resend.side_effect = _resend_error()
    send_reset_email_task.delay("dlq-redis@test.com", "tok")

    assert rd.call_count == 1
    payload = rd.call_args.args[0]
    assert payload["recipient_redacted"] == "d***@test.com"
    assert payload["task_id"]
    assert payload["error"]


# ─────────────────────── Sentry gate (both branches) ────────────

def test_sentry_captures_when_dsn_set(mock_resend, monkeypatch):
    """Positive branch: DSN non-empty → capture_exception fires once."""
    monkeypatch.setattr(
        email_task.settings, "SENTRY_DSN",
        "https://fake@o0.ingest.sentry.io/0",
    )
    mock_resend.side_effect = _resend_error()

    with patch.object(
        email_task.sentry_sdk, "capture_exception"
    ) as cap:
        send_reset_email_task.delay("sentry-on@test.com", "tok")

    assert cap.call_count == 1


def test_sentry_not_called_when_dsn_unset(mock_resend, monkeypatch):
    """Negative branch: DSN empty → capture_exception NEVER called.

    Guards against regressions where the SENTRY_DSN check is removed
    and Sentry begins receiving events from unconfigured environments.
    """
    monkeypatch.setattr(email_task.settings, "SENTRY_DSN", "")
    mock_resend.side_effect = _resend_error()

    with patch.object(
        email_task.sentry_sdk, "capture_exception"
    ) as cap:
        send_reset_email_task.delay("sentry-off@test.com", "tok")

    assert cap.call_count == 0


# ─────────────────────── sink isolation ─────────────────────────

def test_sink_isolation_postgres_failure_does_not_block_redis(
    mock_resend, _mock_dlq_sinks
):
    """Terminus must not cascade-fail: if Postgres sink raises, Redis
    sink still fires. Core G2 invariant from Step 4.4 diagnosis."""
    pg, rd = _mock_dlq_sinks
    pg.side_effect = RuntimeError("db down")
    mock_resend.side_effect = _resend_error()

    send_reset_email_task.delay("iso@test.com", "tok")

    assert pg.call_count == 1, "Postgres sink was not attempted"
    assert rd.call_count == 1, "Redis sink blocked by Postgres failure"


# ─────────────────────── log redaction ──────────────────────────

def test_recipient_redacted_in_retry_warning(mock_resend, caplog):
    """Retry log carries 'recipient_redacted', not raw 'email'.

    PII invariant: log aggregators ship externally; DLQ stores full
    email; logs must redact.
    """
    caplog.set_level(logging.WARNING, logger="app.tasks.email_task")
    mock_resend.side_effect = _resend_error()

    send_reset_email_task.delay("redact-me@example.com", "tok")

    retry_records = [
        r for r in caplog.records
        if r.msg == "send_reset_email_retry"
    ]
    assert len(retry_records) >= 1
    rec = retry_records[0]
    assert hasattr(rec, "recipient_redacted")
    assert rec.recipient_redacted == "r***@example.com"


# ─────────────────────── _redact_email helper ───────────────────

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("jeremie@gmail.com", "j***@gmail.com"),
        ("a@b.co", "a***@b.co"),
        ("", "***"),
        ("no-at-sign", "***"),
        ("@only-domain.com", "***@only-domain.com"),
    ],
)
def test_redact_email_helper(raw, expected):
    assert _redact_email(raw) == expected
