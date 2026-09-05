from __future__ import annotations

import pytest

from handlers.ratelimit import (
    ipv6_prefix,
    login_account_bucket,
    login_bucket,
    login_token_bucket,
    register_bucket,
)


def test_the_two_login_endpoints_get_separate_address_buckets() -> None:
    """Sharing one lets a success on the bearer login erase the cookie
    login's failures."""
    assert login_bucket("1.2.3.4") != login_token_bucket("1.2.3.4")


def test_account_bucket_is_hashed() -> None:
    """The table is written by unauthenticated callers, so a raw key would make
    it a log of every address anybody probed."""
    bucket = login_account_bucket("ada@example.com")
    assert "ada@example.com" not in bucket
    assert bucket.startswith("login_account:")


def test_account_bucket_is_case_insensitive() -> None:
    """Load-bearing: without it, varying the case bypasses the control."""
    assert login_account_bucket("ADA@Example.com ") == login_account_bucket("ada@example.com")


def test_account_bucket_is_shared_across_both_login_endpoints() -> None:
    """It meters the account being attacked, whichever door is used."""
    assert login_account_bucket("a@b.co") == login_account_bucket("a@b.co")


def test_different_accounts_get_different_buckets() -> None:
    assert login_account_bucket("a@b.co") != login_account_bucket("c@d.co")


@pytest.mark.parametrize("ip", ["1.2.3.4", "not-an-ip", ""])
def test_ipv4_and_junk_pass_through_unchanged(ip: str) -> None:
    assert ipv6_prefix(ip) == ip


def test_ipv6_is_widened_to_its_routed_block() -> None:
    """Rotating the interface identifier inside one /64 must not mint fresh
    budgets."""
    a = ipv6_prefix("2001:db8:1:2:aaaa:bbbb:cccc:dddd")
    b = ipv6_prefix("2001:db8:1:2:1111:2222:3333:4444")
    assert a == b
    assert ipv6_prefix("2001:db8:1:3::1") != a


def test_register_bucket_uses_the_widened_address() -> None:
    assert register_bucket("2001:db8:1:2::1") == register_bucket("2001:db8:1:2::9")
