import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import exc
from sqlalchemy.orm import Session

from app.config import get_settings
from app.deps import get_session
from app.generation.stub import StubGenerator
from app.main import create_app
from tests.auth import AUTH_HEADERS, with_api_key
from tests.fakes import UnusedEmbedder


def from_driver(error: Exception) -> exc.OperationalError:
    return exc.OperationalError("SELECT 1", {}, error)


def from_server(sqlstate: str) -> exc.OperationalError:
    return from_driver(psycopg.errors.lookup(sqlstate)("reported by the server"))


UNAVAILABLE = {
    "no sqlstate, failed before a server answered": from_driver(
        psycopg.OperationalError("connection failed")
    ),
    "08006 connection failure": from_server("08006"),
    "57P01 admin shutdown": from_server("57P01"),
    "57P02 crash shutdown": from_server("57P02"),
    "57P03 cannot connect now": from_server("57P03"),
    "pool timeout": exc.TimeoutError("QueuePool limit reached"),
}

FAULTS = {
    "40P01 deadlock": from_server("40P01"),
    "40001 serialization failure": from_server("40001"),
    "57014 query cancelled": from_server("57014"),
    "23505 unique violation": exc.IntegrityError(
        "INSERT", {}, psycopg.errors.lookup("23505")("duplicate key")
    ),
}


def serving(error: Exception) -> TestClient:
    """The real app, whose every database session fails with `error`."""
    app = create_app(
        with_api_key(get_settings()), embedder=UnusedEmbedder(), generator=StubGenerator()
    )

    def failing_session() -> Session:
        raise error

    app.dependency_overrides[get_session] = failing_session
    return TestClient(app, headers=AUTH_HEADERS, raise_server_exceptions=False)


@pytest.mark.parametrize("error", list(UNAVAILABLE.values()), ids=list(UNAVAILABLE))
def test_a_request_that_cannot_reach_the_database_gets_a_503_to_retry(error: Exception) -> None:
    with serving(error) as client:
        response = client.get("/collections")

    assert (response.status_code, response.json()) == (503, {"detail": "database unavailable"})
    assert response.headers["Retry-After"] == "5"
    assert "X-Request-ID" in response.headers


@pytest.mark.parametrize("error", list(FAULTS.values()), ids=list(FAULTS))
def test_a_database_error_that_is_not_an_outage_stays_a_500(error: Exception) -> None:
    """Deadlocks and cancelled queries are operational errors too, but retrying later is
    not what they call for."""
    with serving(error) as client:
        response = client.get("/collections")

    assert response.status_code == 500
    assert "Retry-After" not in response.headers
