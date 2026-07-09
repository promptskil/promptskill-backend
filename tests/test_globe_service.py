"""Globe service tests — Step 3.9.

Coverage:
  get_me: None before claim; username after.
  claim_username: sets; 409 on second claim; 409 taken (case-insensitive).
  immutability: DB trigger rejects username UPDATE.
  contribution gate: create_* with no profile -> 403 username_required.
  create_zone/post/reply happy paths; 404 missing zone/post.
  create_reply: reply-to-reply ok; parent from another post -> 400.
  get_zones: DESC order (explicit created_at); search over title/post/reply;
             DISTINCT; limit out of range -> 400.
  get_thread: posts + flat replies (parent_reply_id); 404 missing zone; order.
"""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.models.globe import GlobePost, GlobeProfile, GlobeZone
from app.models.user import User
from app.services.globe_service import (
    claim_username,
    create_post,
    create_reply,
    create_zone,
    delete_post,
    delete_reply,
    edit_post,
    edit_reply,
    get_hidden_zones,
    get_me,
    get_thread,
    get_zones,
    hide_zone,
    unhide_zone,
)


# ─────────────────────── fixtures / helpers ──────────────────────────────

@pytest_asyncio.fixture(loop_scope="session")
async def user_a(db_session):
    u = User(email=f"globe-a-{uuid4().hex[:8]}@t.local",
             password_hash="$2b$04$" + "a" * 53)
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest_asyncio.fixture(loop_scope="session")
async def user_b(db_session):
    u = User(email=f"globe-b-{uuid4().hex[:8]}@t.local",
             password_hash="$2b$04$" + "a" * 53)
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


async def _profile(db_session, user, username):
    p = GlobeProfile(user_id=user.id, username=username)
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


# ─────────────────────── get_me / claim_username ─────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_get_me_none_before_claim(db_session, user_a):
    assert await get_me(user_a.id, db_session) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_claim_username_sets_and_get_me_returns(db_session, user_a):
    out = await claim_username(user_a.id, "jordan_dev", db_session)
    assert out == "jordan_dev"
    assert await get_me(user_a.id, db_session) == "jordan_dev"


@pytest.mark.asyncio(loop_scope="session")
async def test_claim_username_second_time_409(db_session, user_a):
    await claim_username(user_a.id, "firstname", db_session)
    with pytest.raises(HTTPException) as exc:
        await claim_username(user_a.id, "secondname", db_session)
    assert exc.value.status_code == 409


@pytest.mark.asyncio(loop_scope="session")
async def test_claim_username_taken_case_insensitive_409(
    db_session, user_a, user_b
):
    await claim_username(user_a.id, "Jordan", db_session)
    with pytest.raises(HTTPException) as exc:
        await claim_username(user_b.id, "jordan", db_session)  # different case
    assert exc.value.status_code == 409


@pytest.mark.asyncio(loop_scope="session")
async def test_username_immutable_trigger(db_session, user_a):
    p = await _profile(db_session, user_a, "origname")
    p.username = "newname"
    db_session.add(p)
    with pytest.raises(Exception):
        await db_session.commit()
    await db_session.rollback()


# ─────────────────────── contribution gate (403) ─────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_create_zone_without_profile_403(db_session, user_a):
    with pytest.raises(HTTPException) as exc:
        await create_zone(user_a.id, "startup", "No profile", db_session)
    assert exc.value.status_code == 403


# ─────────────────────── create_zone / post / reply ──────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_create_zone_happy(db_session, user_a):
    await _profile(db_session, user_a, "zoner")
    z = await create_zone(user_a.id, "startup", "First SaaS customers", db_session)
    assert z["title"] == "First SaaS customers"
    assert z["id"] is not None and z["created_at"] is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_create_post_happy(db_session, user_a):
    await _profile(db_session, user_a, "poster")
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "cold outreach worked", db_session)
    assert post["author_username"] == "poster"
    assert post["body"] == "cold outreach worked"
    assert post["replies"] == []


@pytest.mark.asyncio(loop_scope="session")
async def test_create_post_zone_missing_404(db_session, user_a):
    await _profile(db_session, user_a, "poster2")
    with pytest.raises(HTTPException) as exc:
        await create_post(user_a.id, uuid4(), "body", db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_create_reply_and_reply_to_reply(db_session, user_a):
    await _profile(db_session, user_a, "replier")
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "root", db_session)
    r1 = await create_reply(user_a.id, post["id"], "top reply", None, db_session)
    assert r1["parent_reply_id"] is None
    r2 = await create_reply(user_a.id, post["id"], "nested", r1["id"], db_session)
    assert r2["parent_reply_id"] == r1["id"]


@pytest.mark.asyncio(loop_scope="session")
async def test_create_reply_parent_other_post_400(db_session, user_a):
    await _profile(db_session, user_a, "replier2")
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post1 = await create_post(user_a.id, z["id"], "p1", db_session)
    post2 = await create_post(user_a.id, z["id"], "p2", db_session)
    parent = await create_reply(user_a.id, post1["id"], "on p1", None, db_session)
    with pytest.raises(HTTPException) as exc:
        await create_reply(user_a.id, post2["id"], "cross", parent["id"], db_session)
    assert exc.value.status_code == 400


@pytest.mark.asyncio(loop_scope="session")
async def test_create_reply_post_missing_404(db_session, user_a):
    await _profile(db_session, user_a, "replier3")
    with pytest.raises(HTTPException) as exc:
        await create_reply(user_a.id, uuid4(), "body", None, db_session)
    assert exc.value.status_code == 404


# ─────────────────────── get_zones (order / search) ──────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_get_zones_orders_desc(db_session, user_a):
    await _profile(db_session, user_a, "orderer")
    base = datetime.utcnow().replace(microsecond=0)
    for title, secs in [("oldest", 0), ("mid", 10), ("newest", 20)]:
        db_session.add(GlobeZone(
            domain="startup", title=title, author_user_id=user_a.id,
            created_at=base + timedelta(seconds=secs),
        ))
    await db_session.commit()
    result = await get_zones(user_id=user_a.id, limit=20, offset=0, q=None, db=db_session)
    titles = [z["title"] for z in result["zones"]]
    assert titles[:3] == ["newest", "mid", "oldest"]


@pytest.mark.asyncio(loop_scope="session")
async def test_get_zones_search_matches_title(db_session, user_a):
    await _profile(db_session, user_a, "search1")
    await create_zone(user_a.id, "startup", "zzqtitletoken here", db_session)
    result = await get_zones(user_id=user_a.id, limit=20, offset=0, q="zzqtitletoken", db=db_session)
    assert any("zzqtitletoken" in z["title"] for z in result["zones"])


@pytest.mark.asyncio(loop_scope="session")
async def test_get_zones_search_matches_post_body(db_session, user_a):
    await _profile(db_session, user_a, "search2")
    z = await create_zone(user_a.id, "startup", "Neutral", db_session)
    await create_post(user_a.id, z["id"], "body has zzqpostword inside", db_session)
    result = await get_zones(user_id=user_a.id, limit=20, offset=0, q="zzqpostword", db=db_session)
    assert z["id"] in [zz["id"] for zz in result["zones"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_get_zones_search_matches_reply_body(db_session, user_a):
    await _profile(db_session, user_a, "search3")
    z = await create_zone(user_a.id, "startup", "Neutral", db_session)
    post = await create_post(user_a.id, z["id"], "root", db_session)
    await create_reply(user_a.id, post["id"], "zzqreplyword here", None, db_session)
    result = await get_zones(user_id=user_a.id, limit=20, offset=0, q="zzqreplyword", db=db_session)
    assert z["id"] in [zz["id"] for zz in result["zones"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_get_zones_search_distinct(db_session, user_a):
    await _profile(db_session, user_a, "search4")
    z = await create_zone(user_a.id, "startup", "zzqdistinct in title", db_session)
    await create_post(user_a.id, z["id"], "zzqdistinct in body too", db_session)
    result = await get_zones(user_id=user_a.id, limit=20, offset=0, q="zzqdistinct", db=db_session)
    ids = [zz["id"] for zz in result["zones"]]
    assert ids.count(z["id"]) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_get_zones_limit_over_max_400(db_session):
    with pytest.raises(HTTPException) as exc:
        await get_zones(user_id=uuid4(), limit=51, offset=0, q=None, db=db_session)
    assert exc.value.status_code == 400


# ─────────────────────── get_thread ──────────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_get_thread_posts_and_flat_replies(db_session, user_a):
    await _profile(db_session, user_a, "threader")
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "root post", db_session)
    r1 = await create_reply(user_a.id, post["id"], "reply1", None, db_session)
    await create_reply(user_a.id, post["id"], "reply2", r1["id"], db_session)

    result = await get_thread(z["id"], limit=20, offset=0, db=db_session)
    assert result["zone"]["id"] == z["id"]
    assert len(result["posts"]) == 1
    p = result["posts"][0]
    assert p["author_username"] == "threader"
    assert len(p["replies"]) == 2
    parents = {rr["parent_reply_id"] for rr in p["replies"]}
    assert None in parents and r1["id"] in parents  # flat, both levels present


@pytest.mark.asyncio(loop_scope="session")
async def test_get_thread_zone_missing_404(db_session):
    with pytest.raises(HTTPException) as exc:
        await get_thread(uuid4(), limit=20, offset=0, db=db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_get_thread_posts_ordered_asc(db_session, user_a):
    await _profile(db_session, user_a, "threadorder")
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    base = datetime.utcnow().replace(microsecond=0)
    for body, secs in [("first", 0), ("second", 10), ("third", 20)]:
        db_session.add(GlobePost(
            zone_id=z["id"], author_user_id=user_a.id, body=body,
            created_at=base + timedelta(seconds=secs),
        ))
    await db_session.commit()
    result = await get_thread(z["id"], limit=20, offset=0, db=db_session)
    bodies = [p["body"] for p in result["posts"]]
    assert bodies == ["first", "second", "third"]


# ─────────────────────── edit_post / edit_reply ──────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_edit_post_changes_body(db_session, user_a):
    await _profile(db_session, user_a, "editor1")
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "original", db_session)
    result = await edit_post(user_a.id, post["id"], "edited", db_session)
    assert result["body"] == "edited"


@pytest.mark.asyncio(loop_scope="session")
async def test_edit_post_other_user_404(db_session, user_a, user_b):
    await _profile(db_session, user_a, "editor2")
    await _profile(db_session, user_b, "editor2b")
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "original", db_session)
    with pytest.raises(HTTPException) as exc:
        await edit_post(user_b.id, post["id"], "hax", db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_edit_post_missing_404(db_session, user_a):
    await _profile(db_session, user_a, "editor3")
    with pytest.raises(HTTPException) as exc:
        await edit_post(user_a.id, uuid4(), "x", db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_edit_reply_changes_body(db_session, user_a):
    await _profile(db_session, user_a, "editor4")
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "root", db_session)
    reply = await create_reply(user_a.id, post["id"], "original", None, db_session)
    result = await edit_reply(user_a.id, reply["id"], "edited", db_session)
    assert result["body"] == "edited"


@pytest.mark.asyncio(loop_scope="session")
async def test_edit_reply_other_user_404(db_session, user_a, user_b):
    await _profile(db_session, user_a, "editor5")
    await _profile(db_session, user_b, "editor5b")
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "root", db_session)
    reply = await create_reply(user_a.id, post["id"], "original", None, db_session)
    with pytest.raises(HTTPException) as exc:
        await edit_reply(user_b.id, reply["id"], "hax", db_session)
    assert exc.value.status_code == 404


# ─────────────────────── hide / unhide ───────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_hide_excludes_from_own_feed(db_session, user_a):
    await _profile(db_session, user_a, "hider1")
    z = await create_zone(user_a.id, "startup", "zzqhide", db_session)
    await hide_zone(user_a.id, z["id"], db_session)
    result = await get_zones(
        user_id=user_a.id, limit=20, offset=0, q=None, db=db_session
    )
    assert z["id"] not in [zz["id"] for zz in result["zones"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_hide_is_personal(db_session, user_a, user_b):
    await _profile(db_session, user_a, "hider2")
    z = await create_zone(user_a.id, "startup", "zzqpersonal", db_session)
    await hide_zone(user_a.id, z["id"], db_session)
    result = await get_zones(
        user_id=user_b.id, limit=20, offset=0, q=None, db=db_session
    )
    assert z["id"] in [zz["id"] for zz in result["zones"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_hide_idempotent(db_session, user_a):
    await _profile(db_session, user_a, "hider3")
    z = await create_zone(user_a.id, "startup", "zzqidem", db_session)
    await hide_zone(user_a.id, z["id"], db_session)
    r = await hide_zone(user_a.id, z["id"], db_session)
    assert r["hidden"] is True


@pytest.mark.asyncio(loop_scope="session")
async def test_hide_missing_zone_404(db_session, user_a):
    with pytest.raises(HTTPException) as exc:
        await hide_zone(user_a.id, uuid4(), db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_unhide_reincludes(db_session, user_a):
    await _profile(db_session, user_a, "hider4")
    z = await create_zone(user_a.id, "startup", "zzqunhide", db_session)
    await hide_zone(user_a.id, z["id"], db_session)
    await unhide_zone(user_a.id, z["id"], db_session)
    result = await get_zones(
        user_id=user_a.id, limit=20, offset=0, q=None, db=db_session
    )
    assert z["id"] in [zz["id"] for zz in result["zones"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_get_hidden_zones_lists_hidden(db_session, user_a):
    await _profile(db_session, user_a, "hlist1")
    z = await create_zone(user_a.id, "startup", "zzqhlist", db_session)
    await hide_zone(user_a.id, z["id"], db_session)
    result = await get_hidden_zones(user_a.id, db_session)
    assert z["id"] in [zz["id"] for zz in result["zones"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_get_hidden_zones_empty(db_session, user_a):
    result = await get_hidden_zones(user_a.id, db_session)
    assert result["zones"] == []


# ─────────────────────── delete_post / delete_reply ──────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_delete_post_removes_it(db_session, user_a):
    await _profile(db_session, user_a, "del1")
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "gone", db_session)
    await delete_post(user_a.id, post["id"], db_session)
    result = await get_thread(z["id"], limit=20, offset=0, db=db_session)
    assert result["posts"] == []


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_post_other_user_404(db_session, user_a, user_b):
    await _profile(db_session, user_a, "del2")
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "keep", db_session)
    with pytest.raises(HTTPException) as exc:
        await delete_post(user_b.id, post["id"], db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_post_missing_404(db_session, user_a):
    with pytest.raises(HTTPException) as exc:
        await delete_post(user_a.id, uuid4(), db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_post_cascades_replies(db_session, user_a):
    await _profile(db_session, user_a, "del3")
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "root", db_session)
    await create_reply(user_a.id, post["id"], "child", None, db_session)
    await delete_post(user_a.id, post["id"], db_session)
    result = await get_thread(z["id"], limit=20, offset=0, db=db_session)
    assert result["posts"] == []


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_reply_removes_it(db_session, user_a):
    await _profile(db_session, user_a, "del4")
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "root", db_session)
    reply = await create_reply(user_a.id, post["id"], "byebye", None, db_session)
    await delete_reply(user_a.id, reply["id"], db_session)
    result = await get_thread(z["id"], limit=20, offset=0, db=db_session)
    assert result["posts"][0]["replies"] == []
