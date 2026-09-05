"""Bearer authentication for native clients.

A bearer request carries no ambient cookie, so it is exempt from CSRF — there
is nothing for a forged cross-site request to replay. See docs/DECISIONS.md § 7.
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import Depends, Request
from pydantic import BaseModel

from deps import DatabaseDep
from handlers.auth import Credentials, Status, UsersDep, authenticate, require_credentials
from handlers.clientip import client_ip
from handlers.envelope import Envelope, not_found, unauthorized
from handlers.ratelimit import login_token_bucket
from middleware.auth import BearerUser
from models.mobile_token import (
    TOKEN_TTL_SECONDS,
    MobileTokenModel,
    generate_token,
    hash_token,
)
from models.user import PublicUser, UserNotFound


def get_tokens(db: DatabaseDep) -> MobileTokenModel:
    return MobileTokenModel(db)


TokensDep = Annotated[MobileTokenModel, Depends(get_tokens)]


class TokenGrant(BaseModel):
    token: str
    user: PublicUser


def login_token(
    request: Request,
    users: UsersDep,
    tokens: TokensDep,
    body: Annotated[Credentials, Depends(require_credentials)],
) -> Envelope[TokenGrant]:
    """POST /api/v1/auth/login_token

    Its own address bucket, separate from the cookie login's: sharing one lets
    a success here erase the cookie login's failures. See docs/DECISIONS.md § 5.
    """
    user = authenticate(users, body, login_token_bucket(client_ip(request)))
    token = generate_token()
    tokens.issue(hash_token(token), user.id, time.time() + TOKEN_TTL_SECONDS)
    return Envelope(data=TokenGrant(token=token, user=user.public()))


def logout_token(request: Request, tokens: TokensDep) -> Envelope[Status]:
    """DELETE /api/v1/auth/logout_token — revoke the presented token."""
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise unauthorized()
    tokens.revoke(hash_token(header.removeprefix("Bearer ").strip()))
    return Envelope(data=Status(status="logged out"))


def me_token(users: UsersDep, user_id: BearerUser) -> Envelope[PublicUser]:
    """GET /api/v1/auth/me_token"""
    try:
        return Envelope(data=users.find_by_id(user_id).public())
    except UserNotFound:
        raise not_found("user not found") from None
