"""The API and its engine against a database they cannot reach, through the real driver.

Needs none of the Compose services: nothing listens on the port they connect to, or what
listens never speaks.
"""

import socket
import threading
import time

from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.session import build_engine, database_ok
from app.generation.stub import StubGenerator
from app.main import create_app
from tests.auth import AUTH_HEADERS, with_api_key
from tests.fakes import UnusedEmbedder


def test_a_database_refusing_connections_gets_a_503_to_retry() -> None:
    """Port 1 refuses the connection, so the driver fails before any server answers."""
    settings = with_api_key(get_settings().model_copy(update={"postgres_port": 1}))
    app = create_app(settings, embedder=UnusedEmbedder(), generator=StubGenerator())

    with TestClient(app, headers=AUTH_HEADERS) as client:
        response = client.get("/collections")

    assert (response.status_code, response.json()) == (503, {"detail": "database unavailable"})
    assert response.headers["Retry-After"] == "5"


def test_a_database_that_never_answers_is_given_up_on_within_the_timeout() -> None:
    """A listener that accepts connections and never speaks stands in for an address whose
    packets go nowhere. Left to psycopg, a connection waits 130 seconds on either: with
    PostgreSQL stopped in the kind cluster, a worker's poll hung that long."""
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        settings = get_settings().model_copy(
            update={
                "postgres_host": "127.0.0.1",
                "postgres_port": silent.getsockname()[1],
                "database_connect_timeout_seconds": 2,
            }
        )
        engine = build_engine(settings)
        outcome: list[bool] = []
        started = time.monotonic()
        attempt = threading.Thread(target=lambda: outcome.append(database_ok(engine)), daemon=True)
        attempt.start()
        attempt.join(timeout=10)
        elapsed = time.monotonic() - started

    assert outcome == [False], "still connecting after 10 seconds"
    assert elapsed >= 1.5, "gave up at once, so nothing waited on the silent listener"
