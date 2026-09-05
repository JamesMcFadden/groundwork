import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_live_reports_alive(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_ready_reports_ok_when_database_reachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.api.database_ok", lambda engine: True)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok"}}


def test_ready_returns_503_when_database_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pod that cannot reach the database must stop receiving traffic."""
    monkeypatch.setattr("app.api.database_ok", lambda engine: False)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not ready", "checks": {"database": "unavailable"}}
