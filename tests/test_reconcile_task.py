"""Reconciliation task tests — Stripe + sync engine mocked (no real DB writes).

The task's UPDATE filter is broad (all stale Stripe users), so we never run it
against the real DB in tests. Instead we mock sync_engine.connect() and
stripe.Subscription.retrieve to exercise the task's logic in isolation.
"""
from unittest.mock import MagicMock

from app.tasks import reconcile_task


def _fake_conn(rows):
    """Mock connection whose first execute() (the SELECT) returns `rows`."""
    conn = MagicMock()
    select_result = MagicMock()
    select_result.fetchall.return_value = rows
    conn.execute.return_value = select_result
    return conn


def _patch_engine(monkeypatch, conn):
    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    monkeypatch.setattr(reconcile_task.sync_engine, "connect", lambda: cm)


def test_reconcile_syncs_stale_sub(monkeypatch):
    conn = _fake_conn([("u1", "sub_123")])
    _patch_engine(monkeypatch, conn)
    monkeypatch.setattr(
        reconcile_task.stripe.Subscription,
        "retrieve",
        lambda sid: {"status": "active", "current_period_end": 1_900_000_000},
    )
    result = reconcile_task.reconcile_subscriptions_task()
    assert result == {"reconciled": 1, "errors": 0}
    assert conn.commit.called
    assert conn.execute.call_count == 2  # SELECT + UPDATE


def test_reconcile_skips_on_stripe_error(monkeypatch):
    conn = _fake_conn([("u1", "sub_err")])
    _patch_engine(monkeypatch, conn)

    def boom(sid):
        raise RuntimeError("stripe down")

    monkeypatch.setattr(reconcile_task.stripe.Subscription, "retrieve", boom)
    result = reconcile_task.reconcile_subscriptions_task()
    assert result == {"reconciled": 0, "errors": 1}
    assert conn.execute.call_count == 1  # SELECT only; no UPDATE


def test_reconcile_no_stale_rows(monkeypatch):
    conn = _fake_conn([])
    _patch_engine(monkeypatch, conn)
    result = reconcile_task.reconcile_subscriptions_task()
    assert result == {"reconciled": 0, "errors": 0}
