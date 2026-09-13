import re
import uuid
from collections.abc import Sequence

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth import ApiKeyConfigError
from app.config import get_settings
from app.generation.stub import StubGenerator
from app.ingest.chunk import Tokenizer
from app.main import create_app
from tests.auth import AUTH_HEADERS, TEST_API_KEY

# Present in the app today, and asserted by the walk over routes, so no test can pass by
# finding none.
KNOWN_PROTECTED = {
    ("GET", "/collections"),
    ("POST", "/collections"),
    ("POST", "/documents"),
    ("GET", "/jobs/{job_id}"),
    ("POST", "/questions"),
}


class UnusedEmbedder:
    """Stands in for the model, which no request in these tests may reach."""

    @property
    def tokenizer(self) -> Tokenizer:
        raise AssertionError("the embedder was used")

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError("the embedder was used")

    def embed_query(self, text: str) -> list[float]:
        raise AssertionError("the embedder was used")


def build(api_key: str | None) -> FastAPI:
    secret = None if api_key is None else SecretStr(api_key)
    settings = get_settings().model_copy(update={"api_key": secret})
    return create_app(settings, embedder=UnusedEmbedder(), generator=StubGenerator())


def protected_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Every method and path template the app documents outside /health.

    Read from the OpenAPI schema, since FastAPI keeps included routes inside a private
    wrapper in `app.routes`. A route hidden from the schema would be missed.
    """
    routes = sorted(
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if not path.startswith("/health")
        for method in operations
    )
    assert KNOWN_PROTECTED <= set(routes)
    return routes


def fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)


def test_every_route_but_health_refuses_a_request_without_the_key() -> None:
    """Routes are found by walking the app, so one added without the check fails here."""
    app = build(TEST_API_KEY)
    routes = protected_routes(app)

    with TestClient(app) as client:
        admitted = [
            (method, path, response.status_code)
            for method, path in routes
            if (response := client.request(method, fill(path))).status_code != 401
        ]

    assert admitted == []


def test_a_wrong_key_is_refused_exactly_like_a_missing_one() -> None:
    """Same status, same body: the response says nothing about which it was."""
    app = build(TEST_API_KEY)

    with TestClient(app) as client:
        differing = []
        for method, path in protected_routes(app):
            url = fill(path)
            missing = client.request(method, url)
            wrong = client.request(method, url, headers={"X-API-Key": "wrong-key"})
            if (wrong.status_code, wrong.json()) != (missing.status_code, missing.json()):
                differing.append((method, path, wrong.status_code))

    assert differing == []


def test_the_right_key_is_admitted() -> None:
    """An empty body fails validation, which runs only after the key is accepted."""
    with TestClient(build(TEST_API_KEY)) as client:
        response = client.post("/collections", json={}, headers=AUTH_HEADERS)

    assert response.status_code == 422


def test_health_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Probes send no key, and must not be refused for it."""
    monkeypatch.setattr("app.api.health.database_ok", lambda engine: True)

    with TestClient(build(TEST_API_KEY)) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")

    assert (live.status_code, ready.status_code) == (200, 200)


@pytest.mark.parametrize("api_key", [None, ""])
def test_the_api_refuses_to_start_without_a_key(api_key: str | None) -> None:
    """An empty key would admit a request sending an empty header, so it is refused too."""
    with pytest.raises(ApiKeyConfigError, match="API_KEY"), TestClient(build(api_key)):
        pass
