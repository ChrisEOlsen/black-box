"""Security response headers.

The CSP is the backstop behind the JS rules in CLAUDE.md: `script-src 'self'`
means an injected script does not execute even if it reaches the DOM. The
policy can be this strict because every page loads its JS as an external module
and Tailwind compiles to a linked stylesheet — there is no inline script or
style anywhere, and no CDN.

`object-src` and `base-uri` close the two classic `script-src` bypasses: a
plugin document, and a rewritten `<base>` that re-points every relative script
URL.

If an app needs an outside origin, widen the one directive that needs it rather
than dropping the header.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'self'",
    )
)

# One year, and only sent over HTTPS in production — a browser ignores HSTS on
# a plaintext response, and sending it in local development would pin
# http://localhost to HTTPS in the developer's browser.
HSTS_VALUE = "max-age=31536000; includeSubDomains"

SECURITY_HEADERS = {
    "X-Frame-Options": "SAMEORIGIN",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
}


def _is_production() -> bool:
    return os.getenv("APP_ENV") == "production"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value

        # Every /api/ response can carry data belonging to one signed-in user.
        # Without this a shared proxy, or a browser's own heuristics, may hold
        # an authenticated GET and serve it to someone else.
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"

        if _is_production():
            response.headers["Strict-Transport-Security"] = HSTS_VALUE
        return response
