"""Weekly purge task tests — Phase 4 — Step 4.2.

Gate coverage (from /build-checklist):
  - Op 1: sessions WHERE expires_at < NOW()
  - Op 2: password_reset_tokens WHERE used_at IS NOT NULL
          OR expires_at < NOW()
  - Op 3: prompts WHERE deleted_at IS NOT NULL AND
          deleted_at < NOW() - INTERVAL '90 days'
  - Op 4: no orphan cleanup (cascade only, no SQL)
  - 3 Sentry breadcrumbs (Ops 1, 2, 3 only)
  - Row counts logged

Why sync (not async SAVEPOINT):
  The purge task runs via app.database_sync.sync_engine (psycopg2),
  not the async engine. Its conn.commit() writes to the real DB on a
  separate connection than the async SAVEPOINT used by other tests.
  These tests seed via the same sync engine, run the task, and
  perform explicit hard-delete cleanup at teardown. Unique test
  emails + tracked UUIDs prevent cross-test interference.

Autouse mocks (from conftest):
  - mock_resend, _mock_dlq_sinks, _fast_bcrypt, _celery_eager — all
    no-ops for these tests but active anyway. Harmless.
"""
from datetime import datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import delete, select, text

from app.database_sync import SessionLocal, sync_engine
from app.models import PasswordResetToken, Prompt, Session, User
from app.tasks.purge_task import purge_job_task


# ─────────────────────── sync seed / cleanup helper ─────────────────────


class _PurgeFixture:
    """Tracks rows seeded during a test so teardown hard-deletes any
    leftovers (rows NOT caught by the purge filter).

    Why track manually: the async db_session SAVEPOINT cannot roll
    back rows created on a separate sync connection. This class is
    the sync analog — every seeded row's id is stashed so teardown
    can DELETE in reverse FK order.
    """

    def __init__(self) -> None:
        self.user_ids: list = []
        self.session_ids: list = []
        self.reset_token_ids: list = []
        self.prompt_ids: list = []

    def create_user(self, email: str) -> User:
        with SessionLocal() as db:
            u = User(email=email, password_hash="$2b$04$placeholderhash")
            db.add(u)
            db.commit()
            db.refresh(u)
            self.user_ids.append(u.id)
            return u

    def create_session(self, user_id, *, expires_at: datetime) -> Session:
        with SessionLocal() as db:
            s = Session(
                user_id=user_id,
                token=f"tok-{uuid4()}",
                expires_at=expires_at,
            )
            db.add(s)
            db.commit()
            db.refresh(s)
            self.session_ids.append(s.id)
            return s

    def create_reset_token(
        self, user_id, *, expires_at: datetime, used_at=None
    ) -> PasswordResetToken:
        with SessionLocal() as db:
            t = PasswordResetToken(
                user_id=user_id,
                token=str(uuid4()),
                expires_at=expires_at,
                used_at=used_at,
            )
            db.add(t)
            db.commit()
            db.refresh(t)
            self.reset_token_ids.append(t.id)
            return t

    def create_prompt(self, user_id, *, deleted_at) -> Prompt:
        with SessionLocal() as db:
            p = Prompt(
                user_id=user_id,
                model="claude",
                topic="test",
                prompt_text="test",
                system_prompt_version="v1",
                app_version="0.1.0",
                deleted_at=deleted_at,
            )
            db.add(p)
            db.commit()
            db.refresh(p)
            self.prompt_ids.append(p.id)
            return p

    def cleanup(self) -> None:
        """Reverse-FK hard delete for anything the purge left behind."""
        with SessionLocal() as db:
            if self.prompt_ids:
                db.execute(
                    delete(Prompt).where(Prompt.id.in_(self.prompt_ids))
                )
            if self.reset_token_ids:
                db.execute(
                    delete(PasswordResetToken).where(
                        PasswordResetToken.id.in_(self.reset_token_ids)
                    )
                )
            if self.session_ids:
                db.execute(
                    delete(Session).where(Session.id.in_(self.session_ids))
                )
            if self.user_ids:
                db.execute(delete(User).where(User.id.in_(self.user_ids)))
            db.commit()


def _fixture():
    """Context-manager-style helper — avoids pytest-asyncio fixture
    complications for sync tests."""
    return _PurgeFixture()


def _count_ids_remaining(table_cls, ids: list) -> int:
    """Returns how many of the given ids still exist in the table."""
    if not ids:
        return 0
    with sync_engine.connect() as conn:
        result = conn.execute(
            select(table_cls.id).where(table_cls.id.in_(ids))
        )
        return len(result.all())


# ─────────────────────── Op 1: expired sessions ─────────────────────────

def test_op1_deletes_expired_keeps_active():
    f = _fixture()
    try:
        user = f.create_user(f"purge-op1-{uuid4()}@test.com")
        now = datetime.utcnow()
        expired = f.create_session(user.id, expires_at=now - timedelta(days=1))
        active = f.create_session(user.id, expires_at=now + timedelta(days=1))

        result = purge_job_task()

        assert result["op1_sessions"] >= 1
        # expired row deleted
        assert _count_ids_remaining(Session, [expired.id]) == 0
        # active row preserved
        assert _count_ids_remaining(Session, [active.id]) == 1
    finally:
        f.cleanup()


# ─────────────────────── Op 2: reset tokens ─────────────────────────────

def test_op2_deletes_used_even_if_not_expired():
    """used_at IS NOT NULL → purged regardless of expires_at."""
    f = _fixture()
    try:
        user = f.create_user(f"purge-op2u-{uuid4()}@test.com")
        now = datetime.utcnow()
        used_but_fresh = f.create_reset_token(
            user.id,
            expires_at=now + timedelta(hours=1),  # not expired
            used_at=now - timedelta(minutes=5),   # but used
        )

        purge_job_task()

        assert _count_ids_remaining(
            PasswordResetToken, [used_but_fresh.id]
        ) == 0
    finally:
        f.cleanup()


def test_op2_deletes_expired_even_if_unused():
    """expires_at < NOW() → purged regardless of used_at."""
    f = _fixture()
    try:
        user = f.create_user(f"purge-op2e-{uuid4()}@test.com")
        now = datetime.utcnow()
        expired_unused = f.create_reset_token(
            user.id,
            expires_at=now - timedelta(hours=1),
            used_at=None,
        )
        active_unused = f.create_reset_token(
            user.id,
            expires_at=now + timedelta(hours=1),
            used_at=None,
        )

        purge_job_task()

        assert _count_ids_remaining(
            PasswordResetToken, [expired_unused.id]
        ) == 0
        assert _count_ids_remaining(
            PasswordResetToken, [active_unused.id]
        ) == 1
    finally:
        f.cleanup()


# ─────────────────────── Op 3: 90-day soft-delete retention ─────────────

def test_op3_ninety_day_boundary():
    """91-day-old soft-deleted prompt purged; 89-day-old kept;
    not-yet-soft-deleted kept regardless of age."""
    f = _fixture()
    try:
        user = f.create_user(f"purge-op3-{uuid4()}@test.com")
        now = datetime.utcnow()

        old = f.create_prompt(user.id, deleted_at=now - timedelta(days=91))
        recent = f.create_prompt(user.id, deleted_at=now - timedelta(days=89))
        alive = f.create_prompt(user.id, deleted_at=None)

        purge_job_task()

        assert _count_ids_remaining(Prompt, [old.id]) == 0
        assert _count_ids_remaining(Prompt, [recent.id]) == 1
        assert _count_ids_remaining(Prompt, [alive.id]) == 1
    finally:
        f.cleanup()


# ─────────────────────── Sentry breadcrumbs ─────────────────────────────

def test_fires_exactly_three_sentry_breadcrumbs_in_order():
    """Op 1, 2, 3 each emit one breadcrumb. Op 4 does NOT (no SQL)."""
    f = _fixture()
    try:
        user = f.create_user(f"purge-sentry-{uuid4()}@test.com")
        # Minimal seed — breadcrumbs fire regardless of row count
        f.create_session(
            user.id, expires_at=datetime.utcnow() - timedelta(days=1)
        )

        with patch(
            "app.tasks.purge_task.sentry_sdk.add_breadcrumb"
        ) as mock_bc:
            purge_job_task()

        assert mock_bc.call_count == 3
        messages = [c.kwargs["message"] for c in mock_bc.call_args_list]
        assert messages[0].startswith("Purge Op1:")
        assert messages[1].startswith("Purge Op2:")
        assert messages[2].startswith("Purge Op3:")
    finally:
        f.cleanup()


# ─────────────────────── Idempotency on clean DB ────────────────────────

def test_idempotent_on_clean_baseline():
    """Running purge when nothing matches the WHERE clauses must not
    crash. Row counts can be nonzero if prior test data exists in the
    DB — assert only that the task returned a well-formed dict and did
    not raise.
    """
    result = purge_job_task()
    assert set(result.keys()) == {
        "op1_sessions", "op2_reset_tokens", "op3_prompts"
    }
    assert all(isinstance(v, int) and v >= 0 for v in result.values())

    # Second run immediately after first — must succeed.
    result2 = purge_job_task()
    assert set(result2.keys()) == {
        "op1_sessions", "op2_reset_tokens", "op3_prompts"
    }


# ─────────────────────── Celery registration ────────────────────────────

def test_task_registered_on_celery_app():
    """Guards against autodiscover/include drift. Task name must match
    spec so Beat can schedule it by string reference in Phase 4 —
    Step 4.1b."""
    from app.tasks.celery_app import celery_app
    assert "purge_job_task" in celery_app.tasks
