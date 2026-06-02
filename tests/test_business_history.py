"""Business history + membership-validation tests — clean separation.

Covers prompts.business_id stamping semantics:
  - get_business_history returns only rows stamped with this org
  - personal prompts (business_id IS NULL) are excluded
  - prompts stamped for another org are excluded (cross-org isolation)
  - a removed member's org-stamped prompts are retained
  - require_member: member / non-member / unknown business
"""
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete

from app.models.business import Business
from app.models.business_member import BusinessMember
from app.models.prompt import Prompt
from app.models.user import User
from app.services.business_service import get_business_history, require_member

_PW = "$2b$04$" + "a" * 53


async def _make_user(db_session):
    u = User(email=f"biz-{uuid4().hex[:8]}@t.local", password_hash=_PW)
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


async def _make_business(db_session, owner):
    b = Business(owner_id=owner.id, name=f"Org {uuid4().hex[:6]}")
    db_session.add(b)
    await db_session.flush()
    db_session.add(
        BusinessMember(business_id=b.id, user_id=owner.id, role="admin")
    )
    await db_session.commit()
    await db_session.refresh(b)
    return b


async def _add_member(db_session, business, user, role="employee"):
    db_session.add(
        BusinessMember(business_id=business.id, user_id=user.id, role=role)
    )
    await db_session.commit()


async def _make_prompt(db_session, user, business_id=None, topic="t"):
    p = Prompt(
        user_id=user.id,
        model="claude",
        topic=topic,
        prompt_text=f"generated for {topic}",
        system_prompt_version="v1",
        app_version="1.0.0",
        feedback_vote=None,
        business_id=business_id,
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


# ─────────────────────── require_member ─────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_require_member_allows_member(db_session):
    owner = await _make_user(db_session)
    biz = await _make_business(db_session, owner)
    result = await require_member(biz.id, owner.id, db_session)
    assert result.id == biz.id


@pytest.mark.asyncio(loop_scope="session")
async def test_require_member_non_member_403(db_session):
    owner = await _make_user(db_session)
    outsider = await _make_user(db_session)
    biz = await _make_business(db_session, owner)
    with pytest.raises(HTTPException) as exc:
        await require_member(biz.id, outsider.id, db_session)
    assert exc.value.status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_require_member_unknown_business_404(db_session):
    user = await _make_user(db_session)
    with pytest.raises(HTTPException) as exc:
        await require_member(uuid4(), user.id, db_session)
    assert exc.value.status_code == 404


# ─────────────────────── get_business_history scoping ───────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_history_includes_only_org_stamped_prompts(db_session):
    owner = await _make_user(db_session)
    biz = await _make_business(db_session, owner)

    await _make_prompt(db_session, owner, business_id=biz.id, topic="work")
    await _make_prompt(db_session, owner, business_id=None, topic="personal")

    result = await get_business_history(biz.id, owner.id, 20, 0, db_session)
    topics = [i["topic"] for i in result["items"]]
    assert "work" in topics
    assert "personal" not in topics
    assert result["total"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_history_excludes_other_org_prompts(db_session):
    owner_a = await _make_user(db_session)
    owner_b = await _make_user(db_session)
    biz_a = await _make_business(db_session, owner_a)
    biz_b = await _make_business(db_session, owner_b)

    # A member of BOTH orgs generates in each context.
    dual = await _make_user(db_session)
    await _add_member(db_session, biz_a, dual)
    await _add_member(db_session, biz_b, dual)
    await _make_prompt(db_session, dual, business_id=biz_a.id, topic="for-a")
    await _make_prompt(db_session, dual, business_id=biz_b.id, topic="for-b")

    res_a = await get_business_history(biz_a.id, owner_a.id, 20, 0, db_session)
    res_b = await get_business_history(biz_b.id, owner_b.id, 20, 0, db_session)
    assert [i["topic"] for i in res_a["items"]] == ["for-a"]
    assert [i["topic"] for i in res_b["items"]] == ["for-b"]


@pytest.mark.asyncio(loop_scope="session")
async def test_history_retains_removed_member_prompts(db_session):
    owner = await _make_user(db_session)
    biz = await _make_business(db_session, owner)
    member = await _make_user(db_session)
    await _add_member(db_session, biz, member)
    await _make_prompt(db_session, member, business_id=biz.id, topic="kept")

    # Remove the member — membership row deleted.
    await db_session.execute(
        delete(BusinessMember).where(
            BusinessMember.business_id == biz.id,
            BusinessMember.user_id == member.id,
        )
    )
    await db_session.commit()

    result = await get_business_history(biz.id, owner.id, 20, 0, db_session)
    assert "kept" in [i["topic"] for i in result["items"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_history_non_admin_403(db_session):
    owner = await _make_user(db_session)
    biz = await _make_business(db_session, owner)
    member = await _make_user(db_session)
    await _add_member(db_session, biz, member)  # employee, not admin
    with pytest.raises(HTTPException) as exc:
        await get_business_history(biz.id, member.id, 20, 0, db_session)
    assert exc.value.status_code == 403
