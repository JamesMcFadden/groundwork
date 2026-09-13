from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import get_settings
from app.db.session import build_engine, database_ok
from app.generation.stub import StubGenerator
from app.main import create_app
from app.services.embeddings import Embedder
from tests.auth import AUTH_HEADERS, with_api_key


@pytest.fixture
def client(embedder: Embedder) -> Iterator[TestClient]:
    settings = get_settings()
    engine = build_engine(settings)
    if not database_ok(engine):
        pytest.skip("database unavailable; start it with `docker compose up -d postgres`")

    def clear() -> None:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM collections WHERE user_id = :user_id"),
                {"user_id": settings.default_user_id},
            )

    clear()
    # The stub, because startup would otherwise build the real generator, which needs a key.
    app = create_app(with_api_key(settings), embedder=embedder, generator=StubGenerator())
    with TestClient(app, headers=AUTH_HEADERS) as test_client:
        yield test_client
    clear()


def test_create_returns_201_with_the_new_collection(client: TestClient) -> None:
    response = client.post("/collections", json={"name": "engineering"})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "engineering"
    assert body["id"]
    assert body["created_at"]


def test_duplicate_name_returns_409(client: TestClient) -> None:
    client.post("/collections", json={"name": "engineering"})

    response = client.post("/collections", json={"name": "engineering"})

    assert response.status_code == 409


def test_empty_name_returns_422(client: TestClient) -> None:
    response = client.post("/collections", json={"name": ""})

    assert response.status_code == 422


def test_malformed_cursor_returns_400(client: TestClient) -> None:
    response = client.get("/collections", params={"cursor": "not-a-cursor"})

    assert response.status_code == 400


def test_pagination_walks_every_row_exactly_once(client: TestClient) -> None:
    created = {
        client.post("/collections", json={"name": f"collection-{i:02d}"}).json()["id"]
        for i in range(25)
    }

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params = {"limit": 10} | ({"cursor": cursor} if cursor else {})
        body = client.get("/collections", params=params).json()
        seen.extend(item["id"] for item in body["items"])
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert pages == 3
    assert len(seen) == len(set(seen)) == 25
    assert set(seen) == created
