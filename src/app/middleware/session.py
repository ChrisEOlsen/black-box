"""The signed session cookie.

Split out from the auth guards so that `csrf.py` can know the cookie's name,
lifetime and Secure flag without importing anything that touches the database.

The cookie carries `{uid, epo, exp}`, base64url-encoded and HMAC-SHA256 signed.
`epo` is the session epoch at issue time: a cookie minted before a
`logout_all` is behind the stored epoch and dies on the next request, which is
how one call retires every cookie on every device.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import time
from dataclasses import dataclass
from hashlib import sha256

from starlette.requests import Request
from starlette.responses import Response

# NOTE: the name is `gova_session`, not `bb_session`, because
# docs/API-CONTRACT.md is frozen and shared with gova-monolith, and it names
# this cookie. Renaming it here would be a contract amendment, not a tidy-up.
SESSION_COOKIE_NAME = "gova_session"

SESSION_TTL_SECONDS = 24 * 60 * 60

MIN_SECRET_LENGTH = 32


def secure_cookies() -> bool:
    """Read per call, so a value set after import is honored."""
    return os.getenv("APP_ENV") == "production"


def session_secret() -> bytes:
    """The HMAC key. Read per call so tests can set it after import.

    The length check is free next to the HMAC itself, and a short key here is
    the difference between a session nobody can forge and one anybody can.
    """
    key = os.getenv("SESSION_SECRET", "")
    if len(key) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"SESSION_SECRET must be set and at least {MIN_SECRET_LENGTH} characters"
        )
    return key.encode()


@dataclass(frozen=True, slots=True)
class SessionPayload:
    user_id: int
    epoch: int
    expires_at: int


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(encoded: str) -> str:
    return _b64encode(hmac.new(session_secret(), encoded.encode(), sha256).digest())


def set_session(response: Response, user_id: int, epoch: int) -> None:
    """Issue a session cookie bound to the user's current epoch."""
    payload = json.dumps(
        {"uid": user_id, "epo": epoch, "exp": int(time.time()) + SESSION_TTL_SECONDS},
        separators=(",", ":"),
    )
    encoded = _b64encode(payload.encode())
    response.set_cookie(
        SESSION_COOKIE_NAME,
        f"{encoded}|{_sign(encoded)}",
        max_age=SESSION_TTL_SECONDS,
        path="/",
        httponly=True,
        secure=secure_cookies(),
        samesite="strict",
    )


def clear_session(response: Response) -> None:
    """Delete the cookie from this browser.

    It cannot reach copies on other devices — `UserModel.bump_session_epoch` is
    what does that. The attributes must match the cookie being replaced or some
    browsers ignore the deletion.
    """
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=secure_cookies(),
        samesite="strict",
    )


def read_session(request: Request) -> SessionPayload | None:
    """Verify the cookie's signature and expiry, and return its claims.

    The epoch is NOT checked here — that needs the database, and doing it in
    the caller keeps this module a leaf.
    """
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw or "|" not in raw:
        return None
    encoded, _, signature = raw.partition("|")
    if not hmac.compare_digest(signature, _sign(encoded)):
        return None
    try:
        claims = json.loads(_b64decode(encoded))
        payload = SessionPayload(
            user_id=int(claims["uid"]), epoch=int(claims["epo"]), expires_at=int(claims["exp"])
        )
    except (ValueError, KeyError, TypeError):
        return None
    if time.time() > payload.expires_at:
        return None
    return payload
