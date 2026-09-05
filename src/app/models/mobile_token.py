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
        self.db.execute(
            "INSERT INTO mobile_tokens (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, user_id, int(expires_at)),
        )

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
