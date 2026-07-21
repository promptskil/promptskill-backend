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
    get_or_create_domain,
    get_replies,
    get_thread,
    get_zones,
    hide_zone,
    list_domains,
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


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


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
    uname = _uniq("jordan_dev")
    out = await claim_username(user_a.id, uname, db_session)
    assert out == uname
    assert await get_me(user_a.id, db_session) == uname


@pytest.mark.asyncio(loop_scope="session")
async def test_claim_username_second_time_409(db_session, user_a):
    await claim_username(user_a.id, _uniq("firstname"), db_session)
    with pytest.raises(HTTPException) as exc:
        await claim_username(user_a.id, _uniq("secondname"), db_session)
    assert exc.value.status_code == 409


@pytest.mark.asyncio(loop_scope="session")
async def test_claim_username_taken_case_insensitive_409(
    db_session, user_a, user_b
):
    base = _uniq("jordan")
    await claim_username(user_a.id, base.capitalize(), db_session)
    with pytest.raises(HTTPException) as exc:
        await claim_username(user_b.id, base, db_session)  # different case
    assert exc.value.status_code == 409


@pytest.mark.asyncio(loop_scope="session")
async def test_username_immutable_trigger(db_session, user_a):
    p = await _profile(db_session, user_a, _uniq("origname"))
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
    await _profile(db_session, user_a, _uniq("zoner"))
    z = await create_zone(user_a.id, "startup", "First SaaS customers", db_session)
    assert z["title"] == "First SaaS customers"
    assert z["id"] is not None and z["created_at"] is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_create_post_happy(db_session, user_a):
    uname = _uniq("poster")
    await _profile(db_session, user_a, uname)
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "cold outreach worked", db_session)
    assert post["author_username"] == uname
    assert post["body"] == "cold outreach worked"
    assert post["replies"] == []


@pytest.mark.asyncio(loop_scope="session")
async def test_create_post_zone_missing_404(db_session, user_a):
    await _profile(db_session, user_a, _uniq("poster2"))
    with pytest.raises(HTTPException) as exc:
        await create_post(user_a.id, uuid4(), "body", db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_create_reply_and_reply_to_reply(db_session, user_a):
    await _profile(db_session, user_a, _uniq("replier"))
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "root", db_session)
    r1 = await create_reply(user_a.id, post["id"], "top reply", None, db_session)
    assert r1["parent_reply_id"] is None
    r2 = await create_reply(user_a.id, post["id"], "nested", r1["id"], db_session)
    assert r2["parent_reply_id"] == r1["id"]


@pytest.mark.asyncio(loop_scope="session")
async def test_create_reply_parent_other_post_400(db_session, user_a):
    await _profile(db_session, user_a, _uniq("replier2"))
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post1 = await create_post(user_a.id, z["id"], "p1", db_session)
    post2 = await create_post(user_a.id, z["id"], "p2", db_session)
    parent = await create_reply(user_a.id, post1["id"], "on p1", None, db_session)
    with pytest.raises(HTTPException) as exc:
        await create_reply(user_a.id, post2["id"], "cross", parent["id"], db_session)
    assert exc.value.status_code == 400


@pytest.mark.asyncio(loop_scope="session")
async def test_create_reply_post_missing_404(db_session, user_a):
    await _profile(db_session, user_a, _uniq("replier3"))
    with pytest.raises(HTTPException) as exc:
        await create_reply(user_a.id, uuid4(), "body", None, db_session)
    assert exc.value.status_code == 404


# ─────────────────────── get_zones (order / search) ──────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_get_zones_orders_desc(db_session, user_a):
    await _profile(db_session, user_a, _uniq("orderer"))
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
    await _profile(db_session, user_a, _uniq("search1"))
    await create_zone(user_a.id, "startup", "zzqtitletoken here", db_session)
    result = await get_zones(user_id=user_a.id, limit=20, offset=0, q="zzqtitletoken", db=db_session)
    assert any("zzqtitletoken" in z["title"] for z in result["zones"])


@pytest.mark.asyncio(loop_scope="session")
async def test_get_zones_search_matches_post_body(db_session, user_a):
    await _profile(db_session, user_a, _uniq("search2"))
    z = await create_zone(user_a.id, "startup", "Neutral", db_session)
    await create_post(user_a.id, z["id"], "body has zzqpostword inside", db_session)
    result = await get_zones(user_id=user_a.id, limit=20, offset=0, q="zzqpostword", db=db_session)
    assert z["id"] in [zz["id"] for zz in result["zones"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_get_zones_search_matches_reply_body(db_session, user_a):
    await _profile(db_session, user_a, _uniq("search3"))
    z = await create_zone(user_a.id, "startup", "Neutral", db_session)
    post = await create_post(user_a.id, z["id"], "root", db_session)
    await create_reply(user_a.id, post["id"], "zzqreplyword here", None, db_session)
    result = await get_zones(user_id=user_a.id, limit=20, offset=0, q="zzqreplyword", db=db_session)
    assert z["id"] in [zz["id"] for zz in result["zones"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_get_zones_search_distinct(db_session, user_a):
    await _profile(db_session, user_a, _uniq("search4"))
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
    uname = _uniq("threader")
    await _profile(db_session, user_a, uname)
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "root post", db_session)
    r1 = await create_reply(user_a.id, post["id"], "reply1", None, db_session)
    await create_reply(user_a.id, post["id"], "reply2", r1["id"], db_session)

    result = await get_thread(z["id"], limit=20, offset=0, db=db_session)
    assert result["zone"]["id"] == z["id"]
    assert len(result["posts"]) == 1
    p = result["posts"][0]
    assert p["author_username"] == uname
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
    await _profile(db_session, user_a, _uniq("threadorder"))
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
    await _profile(db_session, user_a, _uniq("editor1"))
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "original", db_session)
    result = await edit_post(user_a.id, post["id"], "edited", db_session)
    assert result["body"] == "edited"


@pytest.mark.asyncio(loop_scope="session")
async def test_edit_post_other_user_404(db_session, user_a, user_b):
    await _profile(db_session, user_a, _uniq("editor2"))
    await _profile(db_session, user_b, _uniq("editor2b"))
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "original", db_session)
    with pytest.raises(HTTPException) as exc:
        await edit_post(user_b.id, post["id"], "hax", db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_edit_post_missing_404(db_session, user_a):
    await _profile(db_session, user_a, _uniq("editor3"))
    with pytest.raises(HTTPException) as exc:
        await edit_post(user_a.id, uuid4(), "x", db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_edit_reply_changes_body(db_session, user_a):
    await _profile(db_session, user_a, _uniq("editor4"))
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "root", db_session)
    reply = await create_reply(user_a.id, post["id"], "original", None, db_session)
    result = await edit_reply(user_a.id, reply["id"], "edited", db_session)
    assert result["body"] == "edited"


@pytest.mark.asyncio(loop_scope="session")
async def test_edit_reply_other_user_404(db_session, user_a, user_b):
    await _profile(db_session, user_a, _uniq("editor5"))
    await _profile(db_session, user_b, _uniq("editor5b"))
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "root", db_session)
    reply = await create_reply(user_a.id, post["id"], "original", None, db_session)
    with pytest.raises(HTTPException) as exc:
        await edit_reply(user_b.id, reply["id"], "hax", db_session)
    assert exc.value.status_code == 404


# ─────────────────────── hide / unhide ───────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_hide_excludes_from_own_feed(db_session, user_a):
    await _profile(db_session, user_a, _uniq("hider1"))
    z = await create_zone(user_a.id, "startup", "zzqhide", db_session)
    await hide_zone(user_a.id, z["id"], db_session)
    result = await get_zones(
        user_id=user_a.id, limit=20, offset=0, q=None, db=db_session
    )
    assert z["id"] not in [zz["id"] for zz in result["zones"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_hide_is_personal(db_session, user_a, user_b):
    await _profile(db_session, user_a, _uniq("hider2"))
    z = await create_zone(user_a.id, "startup", "zzqpersonal", db_session)
    await hide_zone(user_a.id, z["id"], db_session)
    result = await get_zones(
        user_id=user_b.id, limit=20, offset=0, q=None, db=db_session
    )
    assert z["id"] in [zz["id"] for zz in result["zones"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_hide_idempotent(db_session, user_a):
    await _profile(db_session, user_a, _uniq("hider3"))
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
    await _profile(db_session, user_a, _uniq("hider4"))
    z = await create_zone(user_a.id, "startup", "zzqunhide", db_session)
    await hide_zone(user_a.id, z["id"], db_session)
    await unhide_zone(user_a.id, z["id"], db_session)
    result = await get_zones(
        user_id=user_a.id, limit=20, offset=0, q=None, db=db_session
    )
    assert z["id"] in [zz["id"] for zz in result["zones"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_get_hidden_zones_lists_hidden(db_session, user_a):
    await _profile(db_session, user_a, _uniq("hlist1"))
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
    await _profile(db_session, user_a, _uniq("del1"))
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "gone", db_session)
    await delete_post(user_a.id, post["id"], db_session)
    result = await get_thread(z["id"], limit=20, offset=0, db=db_session)
    assert result["posts"] == []


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_post_other_user_404(db_session, user_a, user_b):
    await _profile(db_session, user_a, _uniq("del2"))
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
    await _profile(db_session, user_a, _uniq("del3"))
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "root", db_session)
    await create_reply(user_a.id, post["id"], "child", None, db_session)
    await delete_post(user_a.id, post["id"], db_session)
    result = await get_thread(z["id"], limit=20, offset=0, db=db_session)
    assert result["posts"] == []


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_reply_removes_it(db_session, user_a):
    await _profile(db_session, user_a, _uniq("del4"))
    z = await create_zone(user_a.id, "startup", "Z", db_session)
    post = await create_post(user_a.id, z["id"], "root", db_session)
    reply = await create_reply(user_a.id, post["id"], "byebye", None, db_session)
    await delete_reply(user_a.id, reply["id"], db_session)
    result = await get_thread(z["id"], limit=20, offset=0, db=db_session)
    assert result["posts"][0]["replies"] == []


# ─────────────────────── domains (shared, deduped) ───────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_get_or_create_domain_dedup(db_session):
    a = await get_or_create_domain("crypto", db_session)
    assert a == "Crypto"
    b = await get_or_create_domain("CRYPTO", db_session)
    assert b == "Crypto"  # case-insensitive → same canonical, no duplicate


@pytest.mark.asyncio(loop_scope="session")
async def test_get_or_create_domain_matches_seeded(db_session):
    result = await get_or_create_domain("startup", db_session)
    assert result == "Startup"  # seeded preset, case-insensitive match


@pytest.mark.asyncio(loop_scope="session")
async def test_list_domains_includes_presets(db_session):
    result = await list_domains(None, db_session)
    assert "Startup" in result["domains"]
    assert "Health & Fitness" in result["domains"]


@pytest.mark.asyncio(loop_scope="session")
async def test_list_domains_search(db_session):
    result = await list_domains("found", db_session)
    assert "Founders" in result["domains"]


# ─────────────────────── GLOBE-2 bounded replies ─────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_thread_caps_inline_replies_and_paginates(db_session, user_a):
    await _profile(db_session, user_a, _uniq("capper"))
    zone = await create_zone(user_a.id, "startup", _uniq("Cap zone"), db_session)
    post = await create_post(user_a.id, zone["id"], "root", db_session)
    for i in range(22):
        await create_reply(user_a.id, post["id"], f"r{i}", None, db_session)

    thread = await get_thread(zone["id"], 20, 0, db_session)
    tp = thread["posts"][0]
    assert len(tp["replies"]) == 20            # inline cap is structural
    assert tp["has_more_replies"] is True

    page1 = await get_replies(post["id"], 20, 0, db_session)
    assert len(page1["replies"]) == 20
    assert page1["has_more"] is True
    assert page1["next_offset"] == 20

    page2 = await get_replies(post["id"], 20, 20, db_session)
    assert len(page2["replies"]) == 2
    assert page2["has_more"] is False
    assert page2["next_offset"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_thread_under_cap_no_more(db_session, user_a):
    await _profile(db_session, user_a, _uniq("undercap"))
    zone = await create_zone(user_a.id, "startup", _uniq("Small"), db_session)
    post = await create_post(user_a.id, zone["id"], "root", db_session)
    for i in range(5):
        await create_reply(user_a.id, post["id"], f"r{i}", None, db_session)

    thread = await get_thread(zone["id"], 20, 0, db_session)
    tp = thread["posts"][0]
    assert len(tp["replies"]) == 5
    assert tp["has_more_replies"] is False
    page = await get_replies(post["id"], 20, 0, db_session)
    assert len(page["replies"]) == 5
    assert page["has_more"] is False


@pytest.mark.asyncio(loop_scope="session")
async def test_get_replies_missing_post_404(db_session):
    with pytest.raises(HTTPException) as exc:
        await get_replies(uuid4(), 20, 0, db_session)
    assert exc.value.status_code == 404
