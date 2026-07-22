"""/run/stream router HTTP-boundary tests — Engine 2 streaming SSE."""
from unittest.mock import patch
from uuid import uuid4

import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.auth import require_active_subscription
from app.main import app
from app.services import run_service


@pytest_asyncio.fixture(loop_scope="session")
async def client():
    uid = uuid4()
    app.dependency_overrides[require_active_subscription] = lambda: uid
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _preflight_none(model, user_id):
    return None, None


async def test_run_stream_yields_sse_frames(client):
    async def _astream_ok(model, text, cap, key):
        for ch in ["He", "llo"]:
            yield ch

    with patch.object(run_service, "run_daily_preflight", _preflight_none), \
         patch.object(run_service, "stream_prompt", _astream_ok):
        r = await client.post("/run/stream", json={"model": "chatgpt", "text": "p"})
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert 'data: "He"' in r.text
    assert 'data: "llo"' in r.text
    assert "event: done" in r.text


async def test_run_stream_provider_error_is_sse_error(client):
    async def _astream_err(model, text, cap, key):
        raise HTTPException(
            status_code=502,
            detail={"error": "provider_error", "message": "x"},
        )
        yield  # pragma: no cover — makes this an async generator

    with patch.object(run_service, "run_daily_preflight", _preflight_none), \
         patch.object(run_service, "stream_prompt", _astream_err):
        r = await client.post("/run/stream", json={"model": "chatgpt", "text": "p"})
    assert r.status_code == 200
    assert "event: error" in r.text
    assert "provider_error" in r.text


async def test_run_stream_unexpected_error_is_sse_error(client):
    async def _astream_boom(model, text, cap, key):
        raise RuntimeError("boom")
        yield  # pragma: no cover — makes this an async generator

    with patch.object(run_service, "run_daily_preflight", _preflight_none), \
         patch.object(run_service, "stream_prompt", _astream_boom):
        r = await client.post("/run/stream", json={"model": "chatgpt", "text": "p"})
    assert r.status_code == 200
    assert "event: error" in r.text
    assert "provider_error" in r.text


async def test_run_stream_over_cap_is_http_429(client):
    async def _preflight_over(model, user_id):
        raise HTTPException(
            status_code=429,
            detail={"error": "daily_limit", "message": "x"},
        )

    with patch.object(run_service, "run_daily_preflight", _preflight_over):
        r = await client.post("/run/stream", json={"model": "chatgpt", "text": "p"})
    assert r.status_code == 429
