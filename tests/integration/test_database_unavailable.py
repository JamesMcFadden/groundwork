"""The API against a database it cannot reach at all, through the real driver.

Needs none of the Compose services: nothing listens on the port it connects to.
"""

from fastapi.testclient import TestClient

from app.config import get_settings
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
