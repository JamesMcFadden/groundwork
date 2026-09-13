"""A request id for every request, one log line when it finishes, and connections that
close once the process is draining."""

import logging
import re
import threading
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

# Set once the process is asked to drain, by `app.main.install_drain_signal` on SIGUSR1.
# Defined here rather than in app.main: `python -m app.main` runs that file as `__main__`,
# and uvicorn then imports `app.main` again as a separate module, so a flag defined there
# would be two flags, the signal setting one and the app reading the other.
DRAINING = threading.Event()


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


class DrainMiddleware:
    """Once the process is draining, close each connection after its response.

    Kubernetes takes a deleted pod out of its Service, but a connection a client already
    holds keeps reaching the pod, and uvicorn closes every idle connection the moment it is
    told to stop: a request sent on one as it closes gets no response. Draining answers with
    `Connection: close` first, which uvicorn honours by closing the connection once the
    response is sent, so each client's next request opens a new connection that kube-proxy
    sends to another pod.
    """

    def __init__(self, app: ASGIApp, draining: threading.Event) -> None:
        self.app = app
        self.draining = draining

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_closing(message: Message) -> None:
            if message["type"] == "http.response.start" and self.draining.is_set():
                MutableHeaders(scope=message)["connection"] = "close"
            await send(message)

        await self.app(scope, receive, send_closing)
