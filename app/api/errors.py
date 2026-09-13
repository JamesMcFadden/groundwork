"""A request whose database cannot be reached gets 503, a signal to retry, rather than 500."""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout

logger = logging.getLogger(__name__)

# Seconds a client should wait before trying again: long enough for a restarting database to
# accept connections, short enough not to strand a caller.
RETRY_AFTER_SECONDS = 5

# The server is shutting down, has crashed, or is not yet accepting connections.
_SERVER_UNAVAILABLE = frozenset({"57P01", "57P02", "57P03"})


def add_database_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(OperationalError, _handle_database_error)
    app.add_exception_handler(PoolTimeout, _handle_database_error)


def database_unavailable(exc: Exception) -> bool:
    """Whether a database error means the database cannot be reached, not that a query failed.

    SQLAlchemy raises `OperationalError` for both: deadlocks, serialization failures, and
    cancelled queries are operational errors too, and none is an outage. So only these
    count: an error with no SQLSTATE, which failed before any server answered; a class 08
    connection exception; 57P01–57P03; and a pool timeout, raised when every pooled
    connection stays busy.
    """
    if isinstance(exc, PoolTimeout):
        return True
    if not isinstance(exc, OperationalError):
        return False
    sqlstate: str | None = getattr(exc.orig, "sqlstate", None)
    return sqlstate is None or sqlstate.startswith("08") or sqlstate in _SERVER_UNAVAILABLE


async def _handle_database_error(request: Request, exc: Exception) -> JSONResponse:
    if not database_unavailable(exc):
        # A fault rather than an outage: left to become a 500, logged with its traceback.
        # FastAPI may call this handler twice for one such error, so nothing happens first.
        raise exc
    logger.warning("database unavailable", exc_info=exc)
    return JSONResponse(
        {"detail": "database unavailable"},
        status_code=503,
        headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
    )
