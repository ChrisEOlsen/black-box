"""The version handshake a native client makes at launch.

`min_client_version` is the oldest client build this server will still serve
correctly. The iOS client checks it at launch and blocks itself if its bundle
version is below it, turning an opaque decode failure into an actionable
"update required" prompt. It fails open on any error.
"""

from __future__ import annotations

from pydantic import BaseModel

from handlers.envelope import Envelope

API_VERSION = "1.0.0"
MIN_CLIENT_VERSION = "1.0.0"


class VersionInfo(BaseModel):
    api_version: str
    min_client_version: str


def version() -> Envelope[VersionInfo]:
    """GET /api/v1/_version"""
    return Envelope(
        data=VersionInfo(api_version=API_VERSION, min_client_version=MIN_CLIENT_VERSION)
    )
