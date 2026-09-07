"""Refusing an oversized request body while it streams.

Checking `Content-Length` is not enough. A chunked request declares no length
at all, so a header check passes it and the whole body is buffered anyway —
3 MB went through the previous version untouched. The cap has to be enforced on
the bytes as they arrive, which is the only form of the guarantee that holds
against a client that does not cooperate.

Written as raw ASGI rather than `BaseHTTPMiddleware` because the limit acts on
the receive channel, and BaseHTTPMiddleware does not expose it.

This matters more here than in a multi-worker deployment: the app runs a single
uvicorn worker, so a few streaming connections are a throughput problem for
every other caller, not just a memory one.
"""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 1024 * 1024


async def _no_body() -> Message:
    """A receive channel for a response we generate ourselves."""
    return {"type": "http.disconnect"}


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # An honest oversized client is refused without reading a byte.
        if self._declared_too_large(scope):
            await self._refuse(scope, send)
            return

        received = 0
        exceeded = False
        started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # The app sees a disconnect and unwinds; we answer below.
                    exceeded = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            # Swallow whatever the app was about to say about the disconnect —
            # 413 is the accurate answer, and it is ours to give.
            if exceeded and not started:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        await self.app(scope, limited_receive, guarded_send)

        if exceeded and not started:
            await self._refuse(scope, send)

    def _declared_too_large(self, scope: Scope) -> bool:
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    return int(value) > self.max_bytes
                except ValueError:
                    return False
        return False

    async def _refuse(self, scope: Scope, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "ok": False,
                "error": "request body too large",
                "code": "validation_failed",
            },
        )
        await response(scope, _no_body, send)
