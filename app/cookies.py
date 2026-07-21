"""Session cookie helpers for the web (browser) auth path.

The web session token is delivered as a host-only, HttpOnly cookie set by
api.vaineai.com — same-site with www.vaineai.com so it is sent first-party and
is unreadable by JS (XSS cannot exfiltrate it). Native mobile and the extension
keep using the Authorization bearer header; only the two web origins get a cookie.

Intentionally dependency-free (only fastapi) so both app.auth (reads the cookie
by name) and routers.auth (sets/clears it) can import it without a circular
import — app.services.auth_service already imports app.auth, so this module must
not import auth_service. The caller passes max_age instead.
"""
from fastapi import Request, Response

# Host-only on api.vaineai.com (no Domain attribute).
SESSION_COOKIE_NAME = "vaine_session"

# Only these exact origins receive a session cookie. The extension
# (chrome-extension://…) and native mobile (no Origin) keep bearer auth.
WEB_COOKIE_ORIGINS = {
    "https://www.vaineai.com",
    "https://vaineai.com",
}


def is_web_cookie_origin(request: Request) -> bool:
    """True only for the exact web origins that should receive a cookie."""
    return request.headers.get("origin") in WEB_COOKIE_ORIGINS


def set_session_cookie(response: Response, token: str, max_age: int) -> None:
    """Set the host-only, HttpOnly session cookie (no Domain attribute)."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    """Delete the session cookie (matches on name + path; host-only)."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
