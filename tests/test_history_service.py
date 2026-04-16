"""History service tests — Phase 7 — Step 7.2.

Gate coverage (from /build-checklist Step 7.2):
  - feedback writes vote
  - feedback on other user's prompt → 404
  - feedback on soft-deleted prompt → 404
  - feedback on nonexistent prompt → 404
  - get_history excludes soft-deleted rows
  - get_history orders DESC by created_at
  - get_history pagination (limit/offset)
  - get_history limit out of range → 400
  - soft_delete sets deleted_at
  - soft_delete on other user's prompt → 404
  - double soft_delete → 404 on second call
"""
import asyncio
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select

from app.models.prompt import Prompt, PromptVote
from app.models.user import User
from app.services.history_service import (
    get_history,
    record_feedback,
    soft_delete_prompt,
)


# ─────────────────────── fixtures ───────────────────────────────────────

@pytest_asyncio.fixture(loop_scope="session")
async def user_a(db_session):
    u = User(email=f"hist-a-{uuid4().hex[:8]}@t.local",
             password_hash="$2b$04$" + "a" * 53)
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest_asyncio.fixture(loop_scope="session")
async def user_b(db_session):
    u = User(email=f"hist-b-{uuid4().hex[:8]}@t.local",
             password_hash="$2b$04$" + "a" * 53)
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


async def _make_prompt(db_session, user, topic="t", version="v1"):
    p = Prompt(
        user_id=user.id,
        model="claude",
        topic=topic,
        prompt_text=f"generated for {topic}",
        system_prompt_version=version,
        app_version="1.0.0",
        feedback_vote=None,
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


# ─────────────────────── record_feedback ─────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_record_feedback_writes_vote(db_session, user_a):
    p = await _make_prompt(db_session, user_a)
    result = await record_feedback(user_a.id, p.id, "up", db_session)
    assert result["prompt_id"] == str(p.id)
    assert result["vote"] == "up"

    await db_session.refresh(p)
    assert p.feedback_vote == PromptVote.up


@pytest.mark.asyncio(loop_scope="session")
async def test_record_feedback_other_user_returns_404(
    db_session, user_a, user_b
):
    p = await _make_prompt(db_session, user_a)
    with pytest.raises(HTTPException) as exc:
        await record_feedback(user_b.id, p.id, "up", db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_record_feedback_soft_deleted_returns_404(db_session, user_a):
    p = await _make_prompt(db_session, user_a)
    await soft_delete_prompt(user_a.id, p.id, db_session)
    with pytest.raises(HTTPException) as exc:
        await record_feedback(user_a.id, p.id, "down", db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_record_feedback_nonexistent_returns_404(db_session, user_a):
    with pytest.raises(HTTPException) as exc:
        await record_feedback(user_a.id, uuid4(), "up", db_session)
    assert exc.value.status_code == 404


# ─────────────────────── get_history ─────────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_get_history_excludes_soft_deleted(db_session, user_a):
    p1 = await _make_prompt(db_session, user_a, topic="keep")
    p2 = await _make_prompt(db_session, user_a, topic="gone")
    await soft_delete_prompt(user_a.id, p2.id, db_session)

    result = await get_history(user_a.id, limit=20, offset=0, db=db_session)
    topics = [i["topic"] for i in result["items"]]
    assert "keep" in topics
    assert "gone" not in topics


@pytest.mark.asyncio(loop_scope="session")
async def test_get_history_orders_desc_by_created_at(db_session, user_a):
    """Verify ORDER BY created_at DESC.

    Cannot rely on server_default=func.now() here: Postgres NOW() is
    transaction_timestamp(), frozen for the whole outer SAVEPOINT-wrapped
    transaction. All three rows would receive identical created_at values
    regardless of asyncio.sleep between inserts. Supply explicit
    timestamps so we actually test the ORDER BY clause.
    """
    from app.models.prompt import Prompt
    base = datetime.utcnow().replace(microsecond=0)

    for topic, offset_seconds in [
        ("first", 0),
        ("second", 10),
        ("third", 20),
    ]:
        p = Prompt(
            user_id=user_a.id,
            model="claude",
            topic=topic,
            prompt_text=f"generated for {topic}",
            system_prompt_version="v1",
            app_version="1.0.0",
            feedback_vote=None,
            created_at=base + timedelta(seconds=offset_seconds),
        )
        db_session.add(p)
    await db_session.commit()

    result = await get_history(user_a.id, limit=20, offset=0, db=db_session)
    topics = [i["topic"] for i in result["items"]]
    assert topics == ["third", "second", "first"]


@pytest.mark.asyncio(loop_scope="session")
async def test_get_history_pagination(db_session, user_a):
    for i in range(5):
        await _make_prompt(db_session, user_a, topic=f"p{i}")
        await asyncio.sleep(0.002)

    page1 = await get_history(user_a.id, limit=2, offset=0, db=db_session)
    page2 = await get_history(user_a.id, limit=2, offset=2, db=db_session)

    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 2
    assert page1["total"] == 5
    assert page2["total"] == 5
    # no overlap
    p1_ids = {i["prompt_id"] for i in page1["items"]}
    p2_ids = {i["prompt_id"] for i in page2["items"]}
    assert p1_ids.isdisjoint(p2_ids)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_history_excludes_other_users_rows(
    db_session, user_a, user_b
):
    await _make_prompt(db_session, user_a, topic="a-only")
    await _make_prompt(db_session, user_b, topic="b-only")

    result_a = await get_history(user_a.id, limit=20, offset=0, db=db_session)
    topics_a = [i["topic"] for i in result_a["items"]]
    assert "a-only" in topics_a
    assert "b-only" not in topics_a


@pytest.mark.asyncio(loop_scope="session")
async def test_get_history_limit_over_max_raises_400(db_session, user_a):
    with pytest.raises(HTTPException) as exc:
        await get_history(user_a.id, limit=51, offset=0, db=db_session)
    assert exc.value.status_code == 400


@pytest.mark.asyncio(loop_scope="session")
async def test_get_history_limit_zero_raises_400(db_session, user_a):
    with pytest.raises(HTTPException) as exc:
        await get_history(user_a.id, limit=0, offset=0, db=db_session)
    assert exc.value.status_code == 400


@pytest.mark.asyncio(loop_scope="session")
async def test_get_history_negative_offset_raises_400(db_session, user_a):
    with pytest.raises(HTTPException) as exc:
        await get_history(user_a.id, limit=10, offset=-1, db=db_session)
    assert exc.value.status_code == 400


@pytest.mark.asyncio(loop_scope="session")
async def test_get_history_empty_user_returns_empty(db_session, user_a):
    result = await get_history(user_a.id, limit=20, offset=0, db=db_session)
    assert result["items"] == []
    assert result["total"] == 0


# ─────────────────────── soft_delete_prompt ──────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_soft_delete_sets_deleted_at(db_session, user_a):
    p = await _make_prompt(db_session, user_a)
    result = await soft_delete_prompt(user_a.id, p.id, db_session)
    assert result["prompt_id"] == str(p.id)
    assert result["deleted_at"] is not None

    await db_session.refresh(p)
    assert p.deleted_at is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_soft_delete_other_user_returns_404(
    db_session, user_a, user_b
):
    p = await _make_prompt(db_session, user_a)
    with pytest.raises(HTTPException) as exc:
        await soft_delete_prompt(user_b.id, p.id, db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_double_soft_delete_returns_404(db_session, user_a):
    p = await _make_prompt(db_session, user_a)
    await soft_delete_prompt(user_a.id, p.id, db_session)
    with pytest.raises(HTTPException) as exc:
        await soft_delete_prompt(user_a.id, p.id, db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_soft_delete_nonexistent_returns_404(db_session, user_a):
    with pytest.raises(HTTPException) as exc:
        await soft_delete_prompt(user_a.id, uuid4(), db_session)
    assert exc.value.status_code == 404
