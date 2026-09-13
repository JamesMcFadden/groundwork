"""Structured logs: one JSON object per line, from the API and the worker alike.

The standard library does the logging; this supplies the formatter and the one place a
process configures it. The API's middleware sets a request id, and every line written
while that request runs carries it, so a request's lines can be found together.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# The id of the request being served, or None outside one. Set by RequestLogMiddleware.
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes every record has. Anything else on a record came from `extra=`, and is written
# as a field of its own, except uvicorn's `color_message`: its message again, with terminal
# colour codes.
_RECORD_ATTRIBUTES = frozenset(vars(logging.makeLogRecord({}))) | {
    "message",
    "asctime",
    "color_message",
}


class JsonFormatter(logging.Formatter):
    """Format a record as one line of JSON.

    The request id is read when the record is formatted, which here is in the thread and
    context that wrote it. A handler that formats on another thread, as a queue handler
    does, would lose it.
    """

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        current = request_id.get()
        if current is not None:
            entry["request_id"] = current
        entry.update(
            (key, value) for key, value in vars(record).items() if key not in _RECORD_ATTRIBUTES
        )
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        # Ids and timestamps passed as fields are written as their string forms.
        return json.dumps(entry, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Send every logger's records to stdout as JSON lines, replacing any handlers set.

    Called once by each process's entrypoint and never by `create_app`, so an app built in
    a test leaves pytest's log capture alone.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
