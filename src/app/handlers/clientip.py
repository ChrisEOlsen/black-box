"""Which address a request is attributed to, for rate limiting.

Two failures point in opposite directions, which is why fixing one does not
reveal the other:

- Trusting a forwarding header from any peer lets a direct caller name itself
  and mint a fresh rate-limit bucket per request.
- Ignoring forwarding headers entirely puts every caller behind a reverse proxy
  into one bucket, so five bad logins lock the whole deployment.

One trusted-peer set resolves both. See docs/DECISIONS.md § 6.
"""

from __future__ import annotations

import ipaddress
import os
from functools import lru_cache

from fastapi import Request

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@lru_cache(maxsize=1)
def trusted_networks() -> tuple[Network, ...]:
    """CIDRs whose forwarding headers are believed, from TRUSTED_PROXIES.

    Empty by default: an app with no proxy in front of it must not believe a
    header any client can set.
    """
    raw = os.getenv("TRUSTED_PROXIES", "")
    networks: list[Network] = []
    for part in raw.split(","):
        entry = part.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _is_trusted(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in trusted_networks())


def client_ip(request: Request) -> str:
    """The address to meter this request against."""
    peer = request.client.host if request.client else ""
    if not peer or not _is_trusted(peer):
        # An untrusted peer names only itself.
        return peer

    forwarded = request.headers.get("cf-connecting-ip", "").strip()
    if forwarded:
        return forwarded

    # Right-most, because a client can prepend anything: everything to the left
    # of the first hop we trust is attacker-authored.
    chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    for candidate in reversed(chain):
        if not _is_trusted(candidate):
            return candidate
    return peer
