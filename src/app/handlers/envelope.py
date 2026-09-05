"""The single response shape for every JSON endpoint.

See docs/API-CONTRACT.md — clients depend on this shape, and changing anything
here breaks them.

FastAPI answers with `{"detail": ...}` by default: for a raised HTTPException,
for a request-validation failure, for an unmatched path, and for a wrong
method. All four are re-shaped here. See docs/DECISIONS.md § 5.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, SerializerFunctionWrapHandler, model_serializer
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("app")

# The namespace that answers in the envelope. Human-facing URLs keep the
# browser's ordinary error page — the same judgement RequireAuth and
# RequirePageAuth make.
API_PATH_PREFIX = "/api/"


class Meta(BaseModel):
    """List-window information. Present on list responses only."""

    limit: int
    offset: int
    total: int


class Envelope[T](BaseModel):
    """`{ok, data, meta, error, code, fields}` — with unset members omitted."""

    ok: bool = True
    data: T | None = None
    meta: Meta | None = None
    error: str | None = None
    code: str | None = None
    fields: dict[str, str] | None = None

    @model_serializer(mode="wrap")
    def _omit_unset(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Drop None members, so the wire shape matches the contract exactly.

        Done as a serializer rather than per-route `response_model_exclude_none`
        because a route that forgets the flag would ship `"error": null` on
        every success — a contract break no test on that route would catch.
        """
        serialized: dict[str, Any] = handler(self)
        return {key: value for key, value in serialized.items() if value is not None}


# Machine-readable failure kinds. This list is CLOSED — clients switch on it.
CODE_UNAUTHORIZED = "unauthorized"
CODE_FORBIDDEN = "forbidden"
CODE_NOT_FOUND = "not_found"
CODE_METHOD_NOT_ALLOWED = "method_not_allowed"
CODE_CONFLICT = "conflict"
CODE_VALIDATION_FAILED = "validation_failed"
CODE_RATE_LIMITED = "rate_limited"
CODE_UNAVAILABLE = "unavailable"
CODE_INTERNAL = "internal"

_CODE_BY_STATUS = {
    401: CODE_UNAUTHORIZED,
    403: CODE_FORBIDDEN,
    404: CODE_NOT_FOUND,
    405: CODE_METHOD_NOT_ALLOWED,
    409: CODE_CONFLICT,
    400: CODE_VALIDATION_FAILED,
    413: CODE_VALIDATION_FAILED,
    422: CODE_VALIDATION_FAILED,
    429: CODE_RATE_LIMITED,
    503: CODE_UNAVAILABLE,
}


def code_for_status(status: int) -> str:
    """Map an HTTP status onto the failure kind a client switches on.

    The default is split by class rather than falling through to `internal`: a
    4xx is by definition something about the request, so an unenumerated one is
    validation_failed. Only 5xx is ours.
    """
    if status in _CODE_BY_STATUS:
        return _CODE_BY_STATUS[status]
    return CODE_VALIDATION_FAILED if 400 <= status < 500 else CODE_INTERNAL


class ApiError(Exception):
    """An error that renders as the envelope.

    Raised instead of FastAPI's HTTPException where a handler wants to pin the
    `code` or attach a per-field map; the two are handled identically below.
    """

    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        code: str | None = None,
        fields: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.code = code or code_for_status(status_code)
        self.fields = fields


def not_found(detail: str = "not found") -> ApiError:
    return ApiError(404, detail)


def unauthorized(detail: str = "unauthorized") -> ApiError:
    return ApiError(401, detail)


def conflict(detail: str) -> ApiError:
    return ApiError(409, detail)


def invalid_query(detail: str) -> ApiError:
    return ApiError(422, detail)


def rate_limited(detail: str = "too many attempts, try again later") -> ApiError:
    return ApiError(429, detail)


def internal(detail: str = "internal error") -> ApiError:
    return ApiError(500, detail)


def validation_failed(fields: dict[str, str]) -> ApiError:
    """422 with a per-field failure map."""
    return ApiError(422, summarize_fields(fields), fields=fields)


def summarize_fields(fields: dict[str, str]) -> str:
    """Take the alphabetically first field, so the message is deterministic."""
    if not fields:
        return "validation failed"
    key = sorted(fields)[0]
    return f"{key}: {fields[key]}"


def normalize_data(data: Any) -> Any:
    """Replace a None list with an empty one.

    A list's `data` is never null — a strict decoder binding an array fails on
    null. Generated models return `[]` and `response_model=Envelope[list[T]]`
    makes the guarantee structural; this is the second guard for hand-written
    handlers. See docs/API-CONTRACT.md § Data rules.
    """
    return [] if data is None else data


def error_response(
    status: int,
    detail: str,
    *,
    code: str | None = None,
    fields: dict[str, str] | None = None,
) -> JSONResponse:
    envelope: Envelope[None] = Envelope(
        ok=False,
        error=detail,
        code=code or code_for_status(status),
        fields=fields,
    )
    return JSONResponse(status_code=status, content=envelope.model_dump())


def _wants_envelope(request: Request) -> bool:
    return request.url.path.startswith(API_PATH_PREFIX)


def _field_map(exc: RequestValidationError) -> dict[str, str]:
    """Turn Pydantic's error list into the contract's per-field map.

    `loc` is a tuple like ("body", "name"); the last string element is the
    field. A body that is not an object at all has no field name, so it lands
    under "body" — the same key the client would see for a malformed payload.
    """
    fields: dict[str, str] = {}
    for err in exc.errors():
        parts = [str(p) for p in err.get("loc", ()) if isinstance(p, str)]
        name = parts[-1] if parts else "body"
        fields.setdefault(name, str(err.get("msg", "invalid")))
    return fields


def register_exception_handlers(app: FastAPI) -> None:
    """Install the four handlers that keep every failure in the envelope.

    Starlette types a handler as `(Request, Exception) -> Response` and
    dispatches on the registered class, so each one casts to the type it was
    registered for. `assert isinstance` would say the same thing but vanishes
    under `python -O`.
    """

    async def on_api_error(request: Request, raw: Exception) -> Response:
        exc = cast(ApiError, raw)
        return error_response(exc.status_code, exc.detail, code=exc.code, fields=exc.fields)

    async def on_http_exception(request: Request, raw: Exception) -> Response:
        exc = cast(StarletteHTTPException, raw)
        # A redirect raised as an exception — RequirePageAuth's 303 — carries
        # its Location header and no body.
        if exc.headers and "location" in {k.lower() for k in exc.headers}:
            return Response(status_code=exc.status_code, headers=exc.headers)
        if not _wants_envelope(request):
            return PlainTextResponse(str(exc.detail), status_code=exc.status_code)
        return error_response(exc.status_code, str(exc.detail))

    async def on_validation_error(request: Request, raw: Exception) -> Response:
        fields = _field_map(cast(RequestValidationError, raw))
        return error_response(422, summarize_fields(fields), fields=fields)

    async def on_unhandled(request: Request, exc: Exception) -> Response:
        # The message is deliberately generic: an exception string can carry a
        # query, a path, or a value the caller has no business seeing.
        log.exception("unhandled error at %s", request.url.path)
        return error_response(500, "internal error", code=CODE_INTERNAL)

    app.add_exception_handler(ApiError, on_api_error)
    app.add_exception_handler(StarletteHTTPException, on_http_exception)
    app.add_exception_handler(RequestValidationError, on_validation_error)
    app.add_exception_handler(Exception, on_unhandled)
