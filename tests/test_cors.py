"""CORS credentialed-contract tests (S2 3.5).

The web app must send/receive the HttpOnly session cookie cross-origin
(www.vaineai.com → api.vaineai.com), which requires
Access-Control-Allow-Credentials: true with the specific matched origin
echoed (never a wildcard). Disallowed origins must get no allow-origin header.
"""
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture(loop_scope="session")
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_cors_preflight_allows_web_credentials(client):
    r = await client.options(
        "/auth/login",
        headers={
            "Origin": "https://www.vaineai.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization,x-app-version",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://www.vaineai.com"
    assert r.headers["access-control-allow-credentials"] == "true"


async def test_cors_preflight_rejects_unknown_origin(client):
    r = await client.options(
        "/auth/login",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in r.headers
