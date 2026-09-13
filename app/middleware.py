"""A request id for every request, and one log line when it finishes."""

import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logs import request_id

logger = logging.getLogger("app.access")

REQUEST_ID_HEADER = "X-Request-ID"

# A caller's id is kept only if it is short and plain, so it can neither forge log content
# nor bloat every line; anything else is replaced.
_ACCEPTABLE_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")


class RequestLogMiddleware:
    """Give each request an id, return it, and log the request when it finishes.

    Pure ASGI rather than `BaseHTTPMiddleware`, so the id is set in a context that the
    route, its dependencies, and exception handlers all run inside. The line it writes
    replaces uvicorn's access log. It names the route template rather than the path, so ids
    in a path never split one route's lines apart, and it carries no headers or bodies, so
    the API key and question text never reach the logs.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        current = _incoming_id(scope) or uuid.uuid4().hex
        token = request_id.set(current)
        started = time.perf_counter()
        status: int | None = None

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, current)
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            # The server answers an unhandled exception with a 500, outside this middleware.
            _log_request(scope, status or 500, started, failed=True)
            raise
        else:
            _log_request(scope, status or 500, started, failed=False)
        finally:
            request_id.reset(token)


def _incoming_id(scope: Scope) -> str | None:
    for name, value in scope["headers"]:
        if name == REQUEST_ID_HEADER.lower().encode():
            candidate: str = value.decode("latin-1")
            return candidate if _ACCEPTABLE_ID.fullmatch(candidate) else None
    return None


def _log_request(scope: Scope, status: int, started: float, *, failed: bool) -> None:
    # Routing records the matched route in the scope; a path matching none has no template.
    route = scope.get("route")
    logger.log(
        logging.ERROR if failed else logging.INFO,
        "request",
        extra={
            "method": scope["method"],
            "route": getattr(route, "path", None),
            "status": status,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        },
        exc_info=failed,
    )
