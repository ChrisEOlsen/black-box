"""Double-submit cookie CSRF protection.

The scheme protects a browser riding an ambient credential. A caller with no
cookies at all — a native client, a webhook — has nothing for a forged
cross-site request to replay, and forcing the scheme on it only breaks it. A
bearer request is exempt for the same reason.

Safe methods are ALLOWLISTED, not unsafe ones denylisted, so a method nobody
thought of is verified by default. See docs/DECISIONS.md § 7.
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from middleware.session import SESSION_COOKIE_NAME, SESSION_TTL_SECONDS, secure_cookies

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"

# RFC 9110 safe methods.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Exempt by path because it is the request that ISSUES a bearer token and so
# cannot yet carry one.
BEARER_LOGIN_PATH = "/api/v1/auth/login_token"


def generate_token() -> str:
    return secrets.token_hex(32)


def _is_bearer_request(request: Request) -> bool:
    return request.url.path == BEARER_LOGIN_PATH or request.headers.get(
        "authorization", ""
    ).startswith("Bearer ")


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if _is_bearer_request(request):
            return await call_next(request)

        cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
        has_token = cookie_token is not None
        # A missing cookie leaves this a fresh random the client cannot know,
        # so verification below fails closed rather than being skipped.
        token = cookie_token if cookie_token is not None else generate_token()
        request.state.csrf_token = token

        is_safe = request.method in SAFE_METHODS
        has_session = SESSION_COOKIE_NAME in request.cookies

        if not is_safe and (has_token or has_session):
            sent = request.headers.get(CSRF_HEADER_NAME, "")
            if not hmac.compare_digest(token, sent):
                return JSONResponse(
                    status_code=403,
                    content={"ok": False, "error": "invalid CSRF token", "code": "forbidden"},
                )

        response = await call_next(request)
        if not has_token and is_safe:
            response.set_cookie(
                CSRF_COOKIE_NAME,
                token,
                max_age=SESSION_TTL_SECONDS,
                path="/",
                httponly=False,  # api.js must read it back
                secure=secure_cookies(),
                samesite="strict",
            )
        return response
