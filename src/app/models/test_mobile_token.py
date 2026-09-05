from __future__ import annotations

import time

import pytest

from db.database import Database
from db.testutil import open_test
from models.mobile_token import (
    MobileTokenModel,
    TokenInvalid,
    generate_token,
    hash_token,
)
from models.user import UserModel


def seed_user(db: Database) -> int:
    return UserModel(db).create("Ada", "a@b.co", "correct-horse-battery")


def test_token_is_64_hex_characters() -> None:
    token = generate_token()
    assert len(token) == 64
    assert int(token, 16) >= 0


def test_only_the_hash_is_stored() -> None:
    with open_test() as db:
        user_id = seed_user(db)
        tokens = MobileTokenModel(db)
        raw = generate_token()
        tokens.issue(hash_token(raw), user_id, time.time() + 3600)
        stored = db.scalar("SELECT token_hash FROM mobile_tokens")
        assert stored != raw
        assert stored == hash_token(raw)


def test_issue_then_resolve() -> None:
    with open_test() as db:
        user_id = seed_user(db)
        tokens = MobileTokenModel(db)
        raw = generate_token()
        tokens.issue(hash_token(raw), user_id, time.time() + 3600)
        assert tokens.user_id(hash_token(raw)) == user_id


def test_unknown_token_is_invalid() -> None:
    with open_test() as db:
        with pytest.raises(TokenInvalid):
            MobileTokenModel(db).user_id("nope")


def test_expired_token_is_invalid() -> None:
    with open_test() as db:
        user_id = seed_user(db)
        tokens = MobileTokenModel(db)
        raw = generate_token()
        tokens.issue(hash_token(raw), user_id, time.time() - 1)
        with pytest.raises(TokenInvalid):
            tokens.user_id(hash_token(raw))


def test_revoke_is_idempotent() -> None:
    with open_test() as db:
        tokens = MobileTokenModel(db)
        tokens.revoke("never-existed")  # must not raise


def test_delete_expired_prunes_only_expired() -> None:
    with open_test() as db:
        user_id = seed_user(db)
        tokens = MobileTokenModel(db)
        live, dead = generate_token(), generate_token()
        tokens.issue(hash_token(live), user_id, time.time() + 3600)
        tokens.issue(hash_token(dead), user_id, time.time() - 1)
        assert tokens.delete_expired() == 1
        assert tokens.user_id(hash_token(live)) == user_id
