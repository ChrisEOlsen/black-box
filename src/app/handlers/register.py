"""Account creation.

INFRASTRUCTURE — hand-written, never scaffolded.
"""

from __future__ import annotations

import logging

from fastapi import Request, Response
from pydantic import BaseModel

from handlers.auth import UsersDep
from handlers.clientip import client_ip
from handlers.envelope import Envelope, conflict, internal, validation_failed
from handlers.ratelimit import MAX_ATTEMPTS_PER_IP, check_not_limited, register_bucket
from middleware.session import set_session
from models.user import DuplicateEmail, PasswordTooLong, PublicUser, UserModel

log = logging.getLogger("app")

MIN_PASSWORD_LENGTH = 8


class Registration(BaseModel):
    name: str
    email: str
    password: str


def validate_registration(name: str, email: str, password: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    if not name:
        fields["name"] = "required"
    if not email:
        fields["email"] = "required"
    elif "@" not in email or email.startswith("@") or email.endswith("@"):
        fields["email"] = "must be a valid email address"
    if not password:
        fields["password"] = "required"  # noqa: S105
    elif len(password) < MIN_PASSWORD_LENGTH:
        fields["password"] = f"must be at least {MIN_PASSWORD_LENGTH} characters"
    return fields


def register(
    request: Request, response: Response, users: UsersDep, body: Registration
) -> Envelope[PublicUser]:
    """POST /api/v1/auth/register"""
    name = body.name.strip()
    email = body.email.strip()

    fields = validate_registration(name, email, body.password)
    if fields:
        raise validation_failed(fields)

    # Metered because the 409 below tells the caller whether an address already
    # has an account — the fact login goes to some trouble to hide. Every
    # attempt counts, success included: account creation is the thing being
    # limited.
    bucket = register_bucket(client_ip(request))
    check_not_limited(users, bucket)
    _record(users, bucket)

    try:
        user_id = users.create(name, email, body.password)
    except DuplicateEmail:
        raise conflict("An account with that email already exists.") from None
    except PasswordTooLong:
        raise validation_failed({"password": "must be at most 72 bytes"}) from None
    except Exception:
        log.exception("registration failed")
        raise internal("Registration failed. Please try again.") from None

    set_session(response, user_id, users.session_epoch(user_id))
    return Envelope(data=PublicUser(id=user_id, name=name, email=email))


def _record(users: UserModel, bucket: str) -> None:
    try:
        users.record_attempt(bucket, MAX_ATTEMPTS_PER_IP)
    except Exception:
        log.exception("rate limiter failed to record %r", bucket)
