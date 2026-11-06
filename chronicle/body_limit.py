"""A request-body size limit that runs before any handler or framework parses the body.

Without it the whole attacker-controlled body is consumed before the role and CSRF gates can reject
the caller (security audit run-1, fingerprint write-routes:request-body-before-role-csrf-gate): an
unauthenticated request could hold the single-process worker's memory hostage proportionally to the
body size. The limit has two arms:

- Content-Length fast path: requests that declare more than the maximum are answered 413 before a
  single body byte is read. Browsers, curl and HTTP clients declare the length for uploads.
- Counting receive wrapper: chunked requests without a Content-Length are measured while the
  framework drains the body; the first byte over the limit raises RequestBodyTooLarge, which
  create_app maps to a 413 response.
"""

from __future__ import annotations

import os

from starlette.requests import Request
from starlette.responses import JSONResponse

# 112 MiB: ten evidence files of 10 MiB each plus the form's own fields, the default form limit.
DEFAULT_MAX_BODY_BYTES = 112 * 1024 * 1024


class RequestBodyTooLarge(Exception):
    """A request body exceeded the configured maximum (see CHRONICLE_MAX_BODY_BYTES)."""


def max_body_bytes_from_environment() -> int:
    try:
        return max(0, int(os.environ.get("CHRONICLE_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES)))
    except ValueError:
        return DEFAULT_MAX_BODY_BYTES


def max_body_middleware(max_bytes: int):
    """ASGI middleware that rejects bodies over `max_bytes` as early as the request allows."""

    class MaxBody:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http" or max_bytes <= 0:
                await self.app(scope, receive, send)
                return
            declared = _declared_length(scope)
            if declared is not None and declared > max_bytes:
                response = JSONResponse({"detail": "Der Anfragekörper ist größer als das erlaubte Maximum."}, status_code=413)
                await response(scope, receive, send)
                return

            seen = {"bytes": 0}

            async def counting_receive():
                message = await receive()
                if message["type"] == "http.request":
                    seen["bytes"] += len(message.get("body", b""))
                    if seen["bytes"] > max_bytes:
                        raise RequestBodyTooLarge
                return message

            await self.app(scope, counting_receive, send)

    return MaxBody


def _declared_length(scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _too_large_handler(request: Request, exc: RequestBodyTooLarge) -> JSONResponse:
    return JSONResponse({"detail": "Der Anfragekörper ist größer als das erlaubte Maximum."}, status_code=413)
