"""Per-user understanding layer tests — profile_service.

Gate coverage:
  - compute_profile counts ONLY the caller's rows (cross-user isolation)
  - soft-deleted prompts ARE included in the pattern (product decision)
  - vote_accept_ratio = up / (up + down)
  - upsert then get returns the row; second upsert updates (no duplicate)
  - get_profile is caller-scoped (other user -> None)
  - build_understanding_block: thin/absent profile -> '' (no injection)
  - build_understanding_block: rich profile -> content string w/ signals
  - recompute_profile opens its OWN session and persists

Isolation is the load-bearing guarantee: every read keys on user_id.
"""
import json
from datetime import datetime
from uuid import uuid4

import pytest
import pytest_asyncio

from app.config import settings
from app.models.prompt import Prompt, PromptVote
from app.models.user import User
from app.models.user_profile import UserProfile
from app.services.profile_service import (
    build_understanding_block,
    compute_profile,
    get_profile,
    recompute_profile,
    upsert_profile,
)

# ─────────────────────── fixtures ───────────────────────────────────────

@pytest_asyncio.fixture(loop_scope="session")
async def user_a(db_session):
    u = User(
        email=f"prof-a-{uuid4().hex[:8]}@t.local",
        password_hash="$2b$04$" + "a" * 53,
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest_asyncio.fixture(loop_scope="session")
async def user_b(db_session):
    u = User(
        email=f"prof-b-{uuid4().hex[:8]}@t.local",
        password_hash="$2b$04$" + "a" * 53,
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


async def _make_prompt(
    db_session, user, topic="t", model="claude", vote=None, deleted=False
):
    p = Prompt(
        user_id=user.id,
        model=model,
        topic=topic,
        prompt_text=f"generated for {topic}",
        system_prompt_version="v1",
        app_version="1.0.0",
        feedback_vote=vote,
        deleted_at=datetime.utcnow() if deleted else None,
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


# ─────────────────────── isolation ──────────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_compute_counts_only_own_rows(db_session, user_a, user_b):
    await _make_prompt(db_session, user_a, topic="a1")
    await _make_prompt(db_session, user_a, topic="a2")
    await _make_prompt(db_session, user_b, topic="b1")

    data_a = await compute_profile(user_a.id, db_session)
    data_b = await compute_profile(user_b.id, db_session)

    assert data_a["prompt_count"] == 2
    assert data_b["prompt_count"] == 1
    assert "b1" not in data_a["top_topics"]  # A never sees B's data


@pytest.mark.asyncio(loop_scope="session")
async def test_get_profile_is_caller_scoped(db_session, user_a, user_b):
    await _make_prompt(db_session, user_a, topic="x")
    await upsert_profile(
        user_a.id, await compute_profile(user_a.id, db_session), db_session
    )
    assert await get_profile(user_a.id, db_session) is not None
    assert await get_profile(user_b.id, db_session) is None


# ─────────────────────── soft-delete included ───────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_soft_deleted_included_in_pattern(db_session, user_a):
    await _make_prompt(db_session, user_a, topic="kept")
    await _make_prompt(db_session, user_a, topic="forgotten", deleted=True)

    data = await compute_profile(user_a.id, db_session)

    assert data["prompt_count"] == 2  # soft-deleted STILL counted
    assert "forgotten" in data["top_topics"]


# ─────────────────────── aggregation ────────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_vote_accept_ratio(db_session, user_a):
    await _make_prompt(db_session, user_a, topic="u", vote=PromptVote.up)
    await _make_prompt(db_session, user_a, topic="d", vote=PromptVote.down)

    data = await compute_profile(user_a.id, db_session)

    assert data["vote_accept_ratio"] == 0.5


@pytest.mark.asyncio(loop_scope="session")
async def test_upsert_updates_in_place(db_session, user_a):
    await _make_prompt(db_session, user_a, topic="x")
    await upsert_profile(
        user_a.id, await compute_profile(user_a.id, db_session), db_session
    )
    prof = await get_profile(user_a.id, db_session)
    assert prof is not None and prof.prompt_count == 1

    await _make_prompt(db_session, user_a, topic="y")
    await upsert_profile(
        user_a.id, await compute_profile(user_a.id, db_session), db_session
    )
    prof2 = await get_profile(user_a.id, db_session)
    assert prof2.prompt_count == 2  # updated, not duplicated


# ─────────────────────── understanding block (pure) ─────────────────────

def test_thin_profile_no_injection():
    thin = UserProfile(
        user_id=uuid4(),
        prompt_count=settings.UNDERSTANDING_MIN_HISTORY - 1,
        top_topics=json.dumps(["x"]),
    )
    assert build_understanding_block(thin) == ""
    assert build_understanding_block(None) == ""


def test_rich_profile_emits_signals():
    rich = UserProfile(
        user_id=uuid4(),
        prompt_count=settings.UNDERSTANDING_MIN_HISTORY + 2,
        top_topics=json.dumps(["pricing", "branding"]),
        preferred_model="claude",
        vote_accept_ratio=0.8,
    )
    block = build_understanding_block(rich)
    assert "User context" in block
    assert "pricing" in block
    assert "claude" in block
    assert "80%" in block


# ─────────────────────── recompute (own session) ────────────────────────

class _CtxWrap:
    """Yield the test session without closing it (preserves SAVEPOINT)."""

    def __init__(self, sess):
        self._s = sess

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio(loop_scope="session")
async def test_recompute_persists_via_own_session(
    db_session, user_a, monkeypatch
):
    for i in range(settings.UNDERSTANDING_MIN_HISTORY):
        await _make_prompt(db_session, user_a, topic=f"topic{i}")

    monkeypatch.setattr(
        "app.services.profile_service.AsyncSessionLocal",
        lambda: _CtxWrap(db_session),
    )
    await recompute_profile(user_a.id)

    prof = await get_profile(user_a.id, db_session)
    assert prof is not None
    assert prof.prompt_count >= settings.UNDERSTANDING_MIN_HISTORY
