"""Users, credentials, and the rate-limit ledger.

INFRASTRUCTURE — hand-written, not scaffolded. The scaffolding tools refuse
credential columns outright (see the builder's `schema.py`), because generic
CRUD is a shape where a request that merely omits a field overwrites it. See
docs/DECISIONS.md § 9.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

import bcrypt
from pydantic import BaseModel

from db.database import Database
from models.timestamp import Timestamp

# bcrypt's input limit. It rejects longer input rather than truncating it, so
# the boundary validates and answers 422 instead of surfacing a library error.
MAX_PASSWORD_BYTES = 72

# Both the counting window and the lockout duration, in seconds.
RATE_LIMIT_WINDOW_SECONDS = 15 * 60

# Paid on the unknown-email path so it costs the same as a wrong password, and
# response time cannot be used to enumerate accounts.
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password-for-timing", bcrypt.gensalt())


class DuplicateEmail(Exception):
    """An account with that address already exists."""


class PasswordTooLong(Exception):
    """Longer than bcrypt accepts."""


class UserNotFound(Exception):
    """No such user."""


class PublicUser(BaseModel):
    """What a client is allowed to see. A password hash never appears in a
    response, which is enforced by this type rather than by remembering."""

    id: int
    name: str
    email: str


@dataclass(frozen=True, slots=True)
class User:
    id: int
    name: str
    email: str
    password_hash: str
    created_at: Timestamp

    def public(self) -> PublicUser:
        return PublicUser(id=self.id, name=self.name, email=self.email)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        name=row["name"],
        email=row["email"],
        password_hash=row["password_hash"],
        created_at=row["created_at"],
    )


class UserModel:
    def __init__(self, db: Database) -> None:
        self.db = db

    # --- accounts ---------------------------------------------------------

    def create(self, name: str, email: str, password: str) -> int:
        if len(password.encode()) > MAX_PASSWORD_BYTES:
            raise PasswordTooLong
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        try:
            return self.db.insert(
                "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                (name, normalize_email(email), hashed),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateEmail from exc

    def find_by_email(self, email: str) -> User:
        row = self.db.query_one(
            "SELECT id, name, email, password_hash, created_at FROM users WHERE email = ?",
            (normalize_email(email),),
        )
        if row is None:
            raise UserNotFound
        return _row_to_user(row)

    def find_by_id(self, user_id: int) -> User:
        row = self.db.query_one(
            "SELECT id, name, email, password_hash, created_at FROM users WHERE id = ?",
            (user_id,),
        )
        if row is None:
            raise UserNotFound
        return _row_to_user(row)

    def check_password(self, user: User, password: str) -> bool:
        """Verify a password. Never raises.

        bcrypt refuses input over 72 bytes with a ValueError rather than
        truncating. No account can HAVE such a password (create rejects it), so
        an over-long candidate is simply wrong — but it must answer False, not
        crash the request. Uncaught, it produced a 500 on the login path that
        skipped rate-limit accounting entirely.
        """
        encoded = password.encode()
        if len(encoded) > MAX_PASSWORD_BYTES:
            return False
        return bcrypt.checkpw(encoded, user.password_hash.encode())

    @staticmethod
    def burn_password_time(password: str) -> None:
        """Pay bcrypt's cost on the unknown-email path.

        Without this, a missing account answers measurably faster than a wrong
        password and the login endpoint becomes an account-enumeration oracle.

        Truncated rather than skipped for over-long input: skipping would make
        the unknown-account path fast again for exactly the requests that
        bypass the check_password cost, restoring the timing signal.
        """
        bcrypt.checkpw(password.encode()[:MAX_PASSWORD_BYTES], _DUMMY_HASH)

    # --- sessions ---------------------------------------------------------

    def session_epoch(self, user_id: int) -> int:
        """The user's current epoch, or 0 for an unknown user."""
        value = self.db.scalar("SELECT session_epoch FROM users WHERE id = ?", (user_id,))
        return int(value) if value is not None else 0

    def bump_session_epoch(self, user_id: int) -> None:
        """Invalidate every session issued before now, on every device.

        Called on "log out everywhere", and it is what a password change should
        call too.
        """
        self.db.execute(
            "UPDATE users SET session_epoch = session_epoch + 1 WHERE id = ?", (user_id,)
        )

    # --- rate limiting ----------------------------------------------------

    def is_rate_limited(self, bucket: str, now: float | None = None) -> bool:
        moment = int(now if now is not None else time.time())
        locked_until = self.db.scalar(
            "SELECT locked_until FROM rate_limits WHERE bucket = ?", (bucket,)
        )
        return locked_until is not None and moment < int(locked_until)

    def record_attempt(self, bucket: str, max_attempts: int, now: float | None = None) -> None:
        """Count one attempt, and lock the bucket at `max_attempts`.

        A bucket whose last attempt predates the window starts a fresh count,
        which is what makes the limit a *rate*. A counter that only increments
        turns the budget into a lifetime quota, so the bucket re-locks on its
        next attempt forever — which behind a shared NAT is every user on that
        address.
        """
        moment = int(now if now is not None else time.time())
        window_start = moment - RATE_LIMIT_WINDOW_SECONDS
        locked_until = moment + RATE_LIMIT_WINDOW_SECONDS
        self.db.execute(
            """
            INSERT INTO rate_limits (bucket, attempts, locked_until, updated_at)
            VALUES (?, 1, NULL, ?)
            ON CONFLICT(bucket) DO UPDATE SET
                attempts = CASE WHEN updated_at < ? THEN 1 ELSE attempts + 1 END,
                locked_until = CASE
                    WHEN updated_at < ? THEN NULL
                    WHEN attempts + 1 >= ? THEN ?
                    ELSE locked_until END,
                updated_at = ?
            """,
            (bucket, moment, window_start, window_start, max_attempts, locked_until, moment),
        )

    def clear_attempts(self, bucket: str) -> None:
        """Forget a bucket after the credential it protects was proven."""
        self.db.execute("DELETE FROM rate_limits WHERE bucket = ?", (bucket,))
