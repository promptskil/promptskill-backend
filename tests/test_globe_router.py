"""Globe router HTTP-boundary tests — Step 4.10.

Locks the ASGI boundary for the /globe endpoints. Service-layer semantics
are covered in test_globe_service.py; here we assert status codes + shapes
through the real app with real auth (get_current_user).
"""
from uuid import uuid4

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.main import app

from tests.auth_helpers import signup_verify_login


# ─────────────────────── fixtures / helpers ─────────────────────────────

@pytest_asyncio.fixture(loop_scope="session")
async def client(db_session):
    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _signup(client, db_session, email):
    return await signup_verify_login(client, db_session, email)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _claim(client, token, username):
    r = await client.post("/globe/username", json={"username": username},
                          headers=_auth(token))
    assert r.status_code == 200, r.text
    return r.json()["username"]


async def _create_zone(client, token, title="First SaaS customers"):
    r = await client.post("/globe/zones",
                          json={"domain": "startup", "title": title},
                          headers=_auth(token))
    assert r.status_code == 200, r.text
    return r.json()


# ─────────────────────── /me + /username ─────────────────────────────────

async def test_me_no_auth_401(client):
    r = await client.get("/globe/me")
    assert r.status_code == 401


async def test_me_null_before_claim(client, db_session):
    token, _ = await _signup(client, db_session, "g-me@t.com")
    r = await client.get("/globe/me", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["username"] is None


async def test_claim_username_then_me(client, db_session):
    token, _ = await _signup(client, db_session, "g-claim@t.com")
    assert await _claim(client, token, "jordan_dev") == "jordan_dev"
    r = await client.get("/globe/me", headers=_auth(token))
    assert r.json()["username"] == "jordan_dev"


async def test_claim_username_bad_format_400(client, db_session):
    token, _ = await _signup(client, db_session, "g-bad@t.com")
    r = await client.post("/globe/username", json={"username": "ab"},  # too short
                          headers=_auth(token))
    assert r.status_code == 400


async def test_claim_username_duplicate_409(client, db_session):
    t_a, _ = await _signup(client, db_session, "g-dup-a@t.com")
    t_b, _ = await _signup(client, db_session, "g-dup-b@t.com")
    await _claim(client, t_a, "sharedname")
    r = await client.post("/globe/username", json={"username": "sharedname"},
                          headers=_auth(t_b))
    assert r.status_code == 409


# ─────────────────────── zones ───────────────────────────────────────────

async def test_create_zone_requires_username_403(client, db_session):
    token, _ = await _signup(client, db_session, "g-noname@t.com")
    r = await client.post("/globe/zones",
                          json={"domain": "startup", "title": "No handle"},
                          headers=_auth(token))
    assert r.status_code == 403


async def test_create_zone_no_auth_401(client):
    r = await client.post("/globe/zones",
                          json={"domain": "startup", "title": "x"})
    assert r.status_code == 401


async def test_create_zone_happy(client, db_session):
    token, _ = await _signup(client, db_session, "g-zone@t.com")
    await _claim(client, token, "zoner1")
    z = await _create_zone(client, token, "Finding my first SaaS customers")
    assert z["title"] == "Finding my first SaaS customers"
    assert z["id"] and z["created_at"]


async def test_list_zones_shows_created(client, db_session):
    token, _ = await _signup(client, db_session, "g-list@t.com")
    await _claim(client, token, "lister1")
    z = await _create_zone(client, token, "zzqlisted")
    r = await client.get("/globe/zones", headers=_auth(token))
    assert r.status_code == 200
    assert z["id"] in [zz["id"] for zz in r.json()["zones"]]


async def test_list_zones_limit_over_50_400(client, db_session):
    token, _ = await _signup(client, db_session, "g-limit@t.com")
    r = await client.get("/globe/zones?limit=51", headers=_auth(token))
    assert r.status_code == 400


# ─────────────────────── posts / replies ─────────────────────────────────

async def test_create_post_and_thread(client, db_session):
    token, _ = await _signup(client, db_session, "g-post@t.com")
    await _claim(client, token, "poster1")
    z = await _create_zone(client, token)
    r = await client.post(f"/globe/zones/{z['id']}/posts",
                          json={"body": "cold outreach worked"},
                          headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["author_username"] == "poster1"

    t = await client.get(f"/globe/zones/{z['id']}/posts", headers=_auth(token))
    assert t.status_code == 200
    assert len(t.json()["posts"]) == 1


async def test_create_post_zone_missing_404(client, db_session):
    token, _ = await _signup(client, db_session, "g-postmiss@t.com")
    await _claim(client, token, "poster2")
    r = await client.post(f"/globe/zones/{uuid4()}/posts",
                          json={"body": "x"}, headers=_auth(token))
    assert r.status_code == 404


async def test_list_posts_zone_missing_404(client, db_session):
    token, _ = await _signup(client, db_session, "g-threadmiss@t.com")
    r = await client.get(f"/globe/zones/{uuid4()}/posts", headers=_auth(token))
    assert r.status_code == 404


async def test_reply_and_reply_to_reply(client, db_session):
    token, _ = await _signup(client, db_session, "g-reply@t.com")
    await _claim(client, token, "replier1")
    z = await _create_zone(client, token)
    p = await client.post(f"/globe/zones/{z['id']}/posts",
                          json={"body": "root"}, headers=_auth(token))
    post_id = p.json()["id"]
    r1 = await client.post(f"/globe/posts/{post_id}/replies",
                           json={"body": "top"}, headers=_auth(token))
    assert r1.status_code == 200, r1.text
    reply_id = r1.json()["id"]
    r2 = await client.post(f"/globe/posts/{post_id}/replies",
                           json={"body": "nested", "parent_reply_id": reply_id},
                           headers=_auth(token))
    assert r2.status_code == 200, r2.text
    assert r2.json()["parent_reply_id"] == reply_id


async def test_reply_parent_other_post_400(client, db_session):
    token, _ = await _signup(client, db_session, "g-cross@t.com")
    await _claim(client, token, "replier2")
    z = await _create_zone(client, token)
    p1 = (await client.post(f"/globe/zones/{z['id']}/posts",
                            json={"body": "p1"}, headers=_auth(token))).json()
    p2 = (await client.post(f"/globe/zones/{z['id']}/posts",
                            json={"body": "p2"}, headers=_auth(token))).json()
    parent = (await client.post(f"/globe/posts/{p1['id']}/replies",
                                json={"body": "on p1"},
                                headers=_auth(token))).json()
    r = await client.post(f"/globe/posts/{p2['id']}/replies",
                          json={"body": "cross", "parent_reply_id": parent["id"]},
                          headers=_auth(token))
    assert r.status_code == 400


async def test_reply_post_missing_404(client, db_session):
    token, _ = await _signup(client, db_session, "g-replymiss@t.com")
    await _claim(client, token, "replier3")
    r = await client.post(f"/globe/posts/{uuid4()}/replies",
                          json={"body": "x"}, headers=_auth(token))
    assert r.status_code == 404


# ─────────────────────── edit post / reply ───────────────────────────────

async def test_edit_post_happy(client, db_session):
    token, _ = await _signup(client, db_session, "g-edit@t.com")
    await _claim(client, token, "editor1")
    z = await _create_zone(client, token)
    p = await client.post(f"/globe/zones/{z['id']}/posts",
                          json={"body": "orig"}, headers=_auth(token))
    post_id = p.json()["id"]
    r = await client.patch(f"/globe/posts/{post_id}",
                           json={"body": "edited"}, headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["body"] == "edited"


async def test_edit_post_other_user_404(client, db_session):
    t_a, _ = await _signup(client, db_session, "g-edita@t.com")
    t_b, _ = await _signup(client, db_session, "g-editb@t.com")
    await _claim(client, t_a, "ownerx")
    await _claim(client, t_b, "otherx")
    z = await _create_zone(client, t_a)
    p = await client.post(f"/globe/zones/{z['id']}/posts",
                          json={"body": "orig"}, headers=_auth(t_a))
    post_id = p.json()["id"]
    r = await client.patch(f"/globe/posts/{post_id}",
                           json={"body": "hax"}, headers=_auth(t_b))
    assert r.status_code == 404


async def test_edit_post_no_auth_401(client):
    r = await client.patch(f"/globe/posts/{uuid4()}", json={"body": "x"})
    assert r.status_code == 401


async def test_edit_post_empty_body_400(client, db_session):
    token, _ = await _signup(client, db_session, "g-editempty@t.com")
    await _claim(client, token, "emptyx")
    z = await _create_zone(client, token)
    p = await client.post(f"/globe/zones/{z['id']}/posts",
                          json={"body": "orig"}, headers=_auth(token))
    post_id = p.json()["id"]
    r = await client.patch(f"/globe/posts/{post_id}",
                           json={"body": ""}, headers=_auth(token))
    assert r.status_code == 400


async def test_edit_reply_happy(client, db_session):
    token, _ = await _signup(client, db_session, "g-editreply@t.com")
    await _claim(client, token, "replyeditor")
    z = await _create_zone(client, token)
    p = await client.post(f"/globe/zones/{z['id']}/posts",
                          json={"body": "root"}, headers=_auth(token))
    post_id = p.json()["id"]
    rr = await client.post(f"/globe/posts/{post_id}/replies",
                           json={"body": "orig"}, headers=_auth(token))
    reply_id = rr.json()["id"]
    r = await client.patch(f"/globe/replies/{reply_id}",
                           json={"body": "edited"}, headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["body"] == "edited"


# ─────────────────────── hide / unhide ───────────────────────────────────

async def test_hide_removes_from_feed(client, db_session):
    token, _ = await _signup(client, db_session, "g-hide@t.com")
    await _claim(client, token, "hider1")
    z = await _create_zone(client, token, "zzqhidefeed")
    h = await client.post(f"/globe/zones/{z['id']}/hide", headers=_auth(token))
    assert h.status_code == 200, h.text
    assert h.json()["hidden"] is True
    r = await client.get("/globe/zones", headers=_auth(token))
    assert z["id"] not in [zz["id"] for zz in r.json()["zones"]]


async def test_unhide_restores(client, db_session):
    token, _ = await _signup(client, db_session, "g-unhide@t.com")
    await _claim(client, token, "unhider1")
    z = await _create_zone(client, token, "zzqunhidefeed")
    await client.post(f"/globe/zones/{z['id']}/hide", headers=_auth(token))
    await client.delete(f"/globe/zones/{z['id']}/hide", headers=_auth(token))
    r = await client.get("/globe/zones", headers=_auth(token))
    assert z["id"] in [zz["id"] for zz in r.json()["zones"]]


async def test_hide_missing_zone_404(client, db_session):
    token, _ = await _signup(client, db_session, "g-hidemiss@t.com")
    r = await client.post(f"/globe/zones/{uuid4()}/hide", headers=_auth(token))
    assert r.status_code == 404


async def test_hide_no_auth_401(client):
    r = await client.post(f"/globe/zones/{uuid4()}/hide")
    assert r.status_code == 401


async def test_list_hidden_zones(client, db_session):
    token, _ = await _signup(client, db_session, "g-hlist@t.com")
    await _claim(client, token, "hlister")
    z = await _create_zone(client, token, "zzqrouterhidden")
    await client.post(f"/globe/zones/{z['id']}/hide", headers=_auth(token))
    r = await client.get("/globe/zones/hidden", headers=_auth(token))
    assert r.status_code == 200
    assert z["id"] in [zz["id"] for zz in r.json()["zones"]]
