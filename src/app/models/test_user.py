from __future__ import annotations

import pytest

from db.testutil import open_test
from models.user import (
    MAX_PASSWORD_BYTES,
    RATE_LIMIT_WINDOW_SECONDS,
    DuplicateEmail,
    PasswordTooLong,
    UserModel,
    UserNotFound,
    normalize_email,
)

PASSWORD = "correct-horse-battery"


def test_create_and_find_round_trip() -> None:
    with open_test() as db:
        users = UserModel(db)
        user_id = users.create("Ada", "ada@example.com", PASSWORD)
        found = users.find_by_id(user_id)
        assert found.name == "Ada"
        assert users.check_password(found, PASSWORD)
        assert not users.check_password(found, "wrong")


def test_password_is_hashed_not_stored() -> None:
    with open_test() as db:
        users = UserModel(db)
        users.create("Ada", "ada@example.com", PASSWORD)
        stored = db.scalar("SELECT password_hash FROM users WHERE id = 1")
        assert PASSWORD not in str(stored)
        assert str(stored).startswith("$2b$")


@pytest.mark.parametrize("email", ["Ada@Example.COM", "  ada@example.com  "])
def test_email_is_normalized(email: str) -> None:
    with open_test() as db:
        users = UserModel(db)
        users.create("Ada", email, PASSWORD)
        assert users.find_by_email("ada@example.com").id == 1


def test_duplicate_email_raises() -> None:
    with open_test() as db:
        users = UserModel(db)
        users.create("Ada", "ada@example.com", PASSWORD)
        with pytest.raises(DuplicateEmail):
            users.create("Grace", "ADA@example.com", PASSWORD)


def test_password_over_bcrypts_limit_is_refused() -> None:
    """bcrypt rejects longer input rather than truncating it, so the boundary
    validates instead of surfacing a library error."""
    with open_test() as db:
        with pytest.raises(PasswordTooLong):
            UserModel(db).create("Ada", "a@b.co", "x" * (MAX_PASSWORD_BYTES + 1))


def test_unknown_user_raises() -> None:
    with open_test() as db:
        with pytest.raises(UserNotFound):
            UserModel(db).find_by_email("nobody@example.com")


def test_normalize_email() -> None:
    assert normalize_email("  A@B.CO ") == "a@b.co"


# --- session epoch -------------------------------------------------------


def test_epoch_starts_at_zero_and_bumps() -> None:
    with open_test() as db:
        users = UserModel(db)
        user_id = users.create("Ada", "a@b.co", PASSWORD)
        assert users.session_epoch(user_id) == 0
        users.bump_session_epoch(user_id)
        assert users.session_epoch(user_id) == 1


def test_epoch_of_an_unknown_user_is_zero() -> None:
    with open_test() as db:
        assert UserModel(db).session_epoch(999) == 0


# --- rate limiting -------------------------------------------------------


def test_bucket_locks_at_the_threshold() -> None:
    with open_test() as db:
        users = UserModel(db)
        for _ in range(4):
            users.record_attempt("login:1.2.3.4", 5)
        assert not users.is_rate_limited("login:1.2.3.4")
        users.record_attempt("login:1.2.3.4", 5)
        assert users.is_rate_limited("login:1.2.3.4")


def test_buckets_are_independent() -> None:
    """The key IS the isolation — two endpoints must not share a budget."""
    with open_test() as db:
        users = UserModel(db)
        for _ in range(5):
            users.record_attempt("login:1.2.3.4", 5)
        assert users.is_rate_limited("login:1.2.3.4")
        assert not users.is_rate_limited("login_token:1.2.3.4")


def test_clear_attempts_unlocks() -> None:
    with open_test() as db:
        users = UserModel(db)
        for _ in range(5):
            users.record_attempt("login:1.2.3.4", 5)
        users.clear_attempts("login:1.2.3.4")
        assert not users.is_rate_limited("login:1.2.3.4")


def test_the_limit_is_a_rate_not_a_lifetime_quota() -> None:
    """A counter that only increments re-locks on the next attempt forever —
    which behind a shared NAT is every user on that address."""
    with open_test() as db:
        users = UserModel(db)
        start = 1_000_000.0
        for _ in range(5):
            users.record_attempt("login:1.2.3.4", 5, now=start)
        assert users.is_rate_limited("login:1.2.3.4", now=start)

        after_window = start + RATE_LIMIT_WINDOW_SECONDS + 1
        assert not users.is_rate_limited("login:1.2.3.4", now=after_window)
        users.record_attempt("login:1.2.3.4", 5, now=after_window)
        assert not users.is_rate_limited("login:1.2.3.4", now=after_window)


def test_account_bucket_has_a_larger_budget_than_the_address_bucket() -> None:
    """Set equal, a per-account bucket is cheap denial of service; set far
    above, a lone attacker exhausts their address budget first."""
    with open_test() as db:
        users = UserModel(db)
        for _ in range(5):
            users.record_attempt("login_account:abc", 20)
        assert not users.is_rate_limited("login_account:abc")
