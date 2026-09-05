"""Cookie authentication.

INFRASTRUCTURE — hand-written, never scaffolded. See docs/DECISIONS.md § 9.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, Request, Response
from pydantic import BaseModel

from deps import DatabaseDep
from handlers.clientip import client_ip
from handlers.envelope import Envelope, internal, not_found, unauthorized, validation_failed
from handlers.ratelimit import (
    check_not_limited,
    clear_buckets,
    login_account_bucket,
    login_bucket,
    record_login_failure,
)
from middleware.auth import CurrentUser
from middleware.session import clear_session, set_session
from models.user import PublicUser, User, UserModel, UserNotFound

log = logging.getLogger("app")


def get_users(db: DatabaseDep) -> UserModel:
    return UserModel(db)


UsersDep = Annotated[UserModel, Depends(get_users)]


class Credentials(BaseModel):
    email: str
    password: str


class Status(BaseModel):
    status: str


def require_credentials(body: Credentials) -> Credentials:
    """Reject blank values. A *missing* key is already a 422 from Pydantic; a
    present-but-empty one is not, so it is checked here and answers the same
    way."""
    fields: dict[str, str] = {}
    if not body.email.strip():
        fields["email"] = "required"
    if not body.password:
        # S105 below: a validation message keyed "password", not a credential.
        fields["password"] = "required"  # noqa: S105
    if fields:
        raise validation_failed(fields)
    return Credentials(email=body.email.strip(), password=body.password)


def authenticate(users: UserModel, creds: Credentials, ip_bucket: str) -> User:
    """Check credentials behind both rate-limit buckets.

    The buckets are read before the user lookup, so a locked-out answer costs
    the same and says the same whether or not the address has an account.
    """
    account_bucket = login_account_bucket(creds.email)
    check_not_limited(users, ip_bucket, account_bucket)

    try:
        user = users.find_by_email(creds.email)
    except UserNotFound:
        # Pay bcrypt's cost anyway, or the missing-account path answers
        # measurably faster than the wrong-password one.
        UserModel.burn_password_time(creds.password)
        record_login_failure(users, ip_bucket, account_bucket)
        raise unauthorized("Invalid email or password.") from None

    if not users.check_password(user, creds.password):
        record_login_failure(users, ip_bucket, account_bucket)
        raise unauthorized("Invalid email or password.")

    clear_buckets(users, ip_bucket, account_bucket)
    return user


def login(
    request: Request,
    response: Response,
    users: UsersDep,
    body: Annotated[Credentials, Depends(require_credentials)],
) -> Envelope[PublicUser]:
    """POST /api/v1/auth/login"""
    user = authenticate(users, body, login_bucket(client_ip(request)))
    # The epoch is read after authenticating, so a revocation moments ago is
    # honored and this new cookie is not born stale.
    set_session(response, user.id, users.session_epoch(user.id))
    return Envelope(data=user.public())


def logout(response: Response) -> Envelope[Status]:
    """POST /api/v1/auth/logout — this browser only."""
    clear_session(response)
    return Envelope(data=Status(status="logged out"))


def logout_all(response: Response, users: UsersDep, user_id: CurrentUser) -> Envelope[Status]:
    """POST /api/v1/auth/logout_all — every device, by bumping the epoch every
    issued cookie was signed against."""
    try:
        users.bump_session_epoch(user_id)
    except Exception:
        log.exception("session epoch bump failed for user %s", user_id)
        raise internal("Something went wrong. Try again.") from None
    clear_session(response)
    return Envelope(data=Status(status="logged out everywhere"))


def me(users: UsersDep, user_id: CurrentUser) -> Envelope[PublicUser]:
    """GET /api/v1/auth/me"""
    try:
        return Envelope(data=users.find_by_id(user_id).public())
    except UserNotFound:
        raise not_found("user not found") from None
