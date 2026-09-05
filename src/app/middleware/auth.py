"""Authentication guards, as FastAPI dependencies.

gova-monolith wraps routes in middleware that runs on every request — static
files included — and hits the database for the session epoch each time. A
dependency runs only where it is asked for. See docs/DECISIONS.md § 6.

`require_auth` guards a JSON endpoint with a 401 envelope. `require_page_auth`
guards a human-facing URL with a 303 to /login, because a browser navigation
must not be answered with an error body. The page guard is a COURTESY, not a
boundary: the shell is inert and every datum on it comes from an `/api/v1/`
endpoint, so those are what must carry `auth: true`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from starlette.exceptions import HTTPException as StarletteHTTPException

from deps import DatabaseDep
from handlers.envelope import ApiError
from middleware.session import read_session
from models.mobile_token import MobileTokenModel, TokenInvalid, hash_token
from models.user import UserModel

BEARER_PREFIX = "Bearer "


def cookie_user_id(request: Request, db: DatabaseDep) -> int | None:
    """The signed-in user for a browser request, or None.

    The epoch is checked only after the signature, so the epoch claim is ours.
    A cookie issued before a revocation is behind the stored epoch and dies
    here.
    """
    payload = read_session(request)
    if payload is None:
        return None
    if payload.epoch < UserModel(db).session_epoch(payload.user_id):
        return None
    return payload.user_id


CookieUserID = Annotated[int | None, Depends(cookie_user_id)]


def require_auth(user_id: CookieUserID) -> int:
    """Guard a JSON endpoint. Generated routes attach this when `auth: true`."""
    if user_id is None:
        raise ApiError(401, "unauthorized")
    return user_id


CurrentUser = Annotated[int, Depends(require_auth)]


def require_page_auth(user_id: CookieUserID) -> None:
    """Guard a human-facing URL by redirecting.

    Raised rather than returned because a dependency cannot return a response;
    the envelope's HTTPException handler passes a Location through untouched.
    """
    if user_id is None:
        raise StarletteHTTPException(status_code=303, headers={"Location": "/login"})


def bearer_user_id(request: Request, db: DatabaseDep) -> int | None:
    """The signed-in user for a native request, or None."""
    header = request.headers.get("authorization", "")
    if not header.startswith(BEARER_PREFIX):
        return None
    token = header.removeprefix(BEARER_PREFIX).strip()
    if not token:
        return None
    try:
        return MobileTokenModel(db).user_id(hash_token(token))
    except TokenInvalid:
        return None


def require_bearer_auth(user_id: Annotated[int | None, Depends(bearer_user_id)]) -> int:
    if user_id is None:
        raise ApiError(401, "unauthorized")
    return user_id


BearerUser = Annotated[int, Depends(require_bearer_auth)]
