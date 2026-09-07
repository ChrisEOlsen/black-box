"""Bearer tokens for native clients.

Only the SHA-256 digest is stored. The raw token exists in exactly one
response body and is never persisted, so a database disclosure hands over
nothing a caller could present.
"""

from __future__ import annotations

import secrets
import time
from hashlib import sha256

from db.database import Database

# Tokens are 64 hex characters — see docs/API-CONTRACT.md § Authentication.
TOKEN_BYTES = 32

TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60

# Live bearer tokens kept per user; the oldest are dropped past this.
MAX_TOKENS_PER_USER = 10


class TokenInvalid(Exception):
    """Unknown or expired.

    One exception covers both: telling them apart would confirm that a stolen
    token was once real.
    """


def generate_token() -> str:
    return secrets.token_hex(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return sha256(token.encode()).hexdigest()


class MobileTokenModel:
    def __init__(self, db: Database) -> None:
        self.db = db

    def issue(self, token_hash: str, user_id: int, expires_at: float) -> None:
        # Prune on issue rather than on a schedule: this template has no cron,
        # and issuing is the only moment the table grows. Bounded work — it
        # touches one user's rows plus an indexed sweep of expired ones.
        _ = self.delete_expired()
        self._enforce_user_cap(user_id)
        self.db.execute(
            "INSERT INTO mobile_tokens (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, user_id, int(expires_at)),
        )

    def _enforce_user_cap(self, user_id: int) -> None:
        """Keep the newest MAX_TOKENS_PER_USER and drop the rest.

        Without a cap, a caller with valid credentials can mint unbounded live
        tokens, each independently usable until its own expiry.
        """
        self.db.execute(
            """
            DELETE FROM mobile_tokens
            WHERE user_id = ? AND token_hash NOT IN (
                SELECT token_hash FROM mobile_tokens
                WHERE user_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?
            )
            """,
            (user_id, user_id, MAX_TOKENS_PER_USER - 1),
        )

    def revoke_all_for_user(self, user_id: int) -> int:
        """Drop every bearer token a user holds.

        The cookie epoch bump does not reach these: a token is a row, not a
        signed claim. Without this, "log out everywhere" left every native
        session — and any stolen token — alive for its full 30-day TTL.
        """
        return self.db.execute("DELETE FROM mobile_tokens WHERE user_id = ?", (user_id,))

    def revoke(self, token_hash: str) -> None:
        """An unknown hash is not an error: logout must not reveal whether the
        token existed."""
        self.db.execute("DELETE FROM mobile_tokens WHERE token_hash = ?", (token_hash,))

    def user_id(self, token_hash: str, now: float | None = None) -> int:
        moment = int(now if now is not None else time.time())
        value = self.db.scalar(
            "SELECT user_id FROM mobile_tokens WHERE token_hash = ? AND expires_at > ?",
            (token_hash, moment),
        )
        if value is None:
            raise TokenInvalid
        return int(value)

    def delete_expired(self, now: float | None = None) -> int:
        """Prune the table. Nothing calls this on the request path; wire it to a
        periodic job if the table needs pruning."""
        moment = int(now if now is not None else time.time())
        return self.db.execute("DELETE FROM mobile_tokens WHERE expires_at <= ?", (moment,))
