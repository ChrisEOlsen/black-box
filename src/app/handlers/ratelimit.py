"""Rate-limit bucket keys and the checks that read them.

The key IS the isolation: a successful login deletes its buckets, so two
actions spelling a key the same way are one action to the limiter. Hence one
namespace per endpoint, and two kinds of bucket with deliberately different
budgets. See docs/DECISIONS.md § 5.
"""

from __future__ import annotations

import ipaddress
import logging
from hashlib import sha256

from handlers.envelope import internal, rate_limited
from models.user import UserModel

log = logging.getLogger("app")

MAX_ATTEMPTS_PER_IP = 5
MAX_ATTEMPTS_PER_ACCOUNT = 20


def login_bucket(ip: str) -> str:
    return f"login:{ip}"


def login_token_bucket(ip: str) -> str:
    return f"login_token:{ip}"


def login_account_bucket(email: str) -> str:
    """Meter the account being attacked, shared by both login endpoints.

    Derived from the submitted address before any lookup and counted on the
    unknown-email path too, so it is not an enumeration oracle. Hashed because
    unauthenticated callers write this table and a raw key would make it a log
    of every address anybody probed. Lowercased is load-bearing: without it,
    varying the case bypasses the control entirely.
    """
    digest = sha256(email.strip().lower().encode()).hexdigest()
    return f"login_account:{digest[:32]}"


def register_bucket(ip: str) -> str:
    """Meter account creation, which is also the 409 revealing whether an
    address already has an account. Every attempt counts, not only failures —
    creating accounts is the thing being limited."""
    return f"register:{ipv6_prefix(ip)}"


def ipv6_prefix(ip: str) -> str:
    """Widen an IPv6 address to its /64 — the smallest block an ISP routes as a
    unit — so rotating interface identifiers inside one block cannot mint fresh
    budgets. IPv4 is scarce enough per address and passes through."""
    try:
        parsed = ipaddress.ip_address(ip.strip())
    except ValueError:
        return ip
    if parsed.version == 4:
        return ip
    return str(ipaddress.ip_network(f"{parsed}/64", strict=False).network_address)


def check_not_limited(users: UserModel, *buckets: str) -> None:
    """Raise if any bucket is locked.

    A read failure is a 500, not a 429: "too many attempts" during a database
    outage sends the operator to the wrong place.
    """
    for bucket in buckets:
        try:
            locked = users.is_rate_limited(bucket)
        except Exception:
            log.exception("rate-limit read failed for %r", bucket)
            raise internal("Something went wrong. Try again.") from None
        if locked:
            raise rate_limited("Too many attempts. Try again in 15 minutes.")


def record_login_failure(users: UserModel, ip_bucket: str, account_bucket: str) -> None:
    """Count one failure against both buckets.

    A write failure means the attempt went uncounted and the limiter is
    silently off, so log it — and still try the other bucket.
    """
    _record(users, ip_bucket, MAX_ATTEMPTS_PER_IP)
    _record(users, account_bucket, MAX_ATTEMPTS_PER_ACCOUNT)


def _record(users: UserModel, bucket: str, max_attempts: int) -> None:
    try:
        users.record_attempt(bucket, max_attempts)
    except Exception:
        log.exception("rate limiter failed to record %r", bucket)


def clear_buckets(users: UserModel, *buckets: str) -> None:
    """Forget what a proven credential entitles the caller to forget: their own
    address, and the account they just authenticated as. Never a bucket
    belonging to an account they have not proven they hold."""
    for bucket in buckets:
        try:
            users.clear_attempts(bucket)
        except Exception:
            log.exception("rate limiter failed to clear %r", bucket)
