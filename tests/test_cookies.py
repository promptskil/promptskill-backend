from fastapi import Response
from starlette.requests import Request

from app.cookies import (
    SESSION_COOKIE_NAME,
    clear_session_cookie,
    is_web_cookie_origin,
    set_session_cookie,
)


def _req(origin: str | None) -> Request:
    headers = [(b"origin", origin.encode())] if origin else []
    return Request({"type": "http", "headers": headers})


def test_web_origins_only():
    assert is_web_cookie_origin(_req("https://www.vaineai.com")) is True
    assert is_web_cookie_origin(_req("https://vaineai.com")) is True
    assert is_web_cookie_origin(_req("chrome-extension://abc")) is False
    assert is_web_cookie_origin(_req(None)) is False


def test_set_cookie_attributes():
    resp = Response()
    set_session_cookie(resp, "tok123", 3600)
    h = resp.headers["set-cookie"].lower()
    assert f"{SESSION_COOKIE_NAME}=tok123" in h
    assert "httponly" in h
    assert "secure" in h
    assert "samesite=lax" in h
    assert "path=/" in h
    assert "max-age=3600" in h
    assert "domain=" not in h  # host-only


def test_clear_cookie_expires():
    resp = Response()
    clear_session_cookie(resp)
    h = resp.headers["set-cookie"].lower()
    assert f"{SESSION_COOKIE_NAME}=" in h
    assert "max-age=0" in h
