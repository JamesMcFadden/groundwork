import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import get_settings
from app.db.models import Collection, User
from app.db.session import build_engine, build_session_factory, database_ok
from app.generation.stub import StubGenerator
from app.main import create_app
from app.services.embeddings import Embedder
from tests.auth import AUTH_HEADERS, with_api_key

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
OTHER_USER_EMAIL = "documents-someone-else@example.com"


@pytest.fixture
def client(embedder: Embedder) -> Iterator[TestClient]:
    settings = get_settings()
    engine = build_engine(settings)
    if not database_ok(engine):
        pytest.skip("database unavailable; start it with `docker compose up -d`")

    def clear() -> None:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM collections WHERE user_id = :user_id"),
                {"user_id": settings.default_user_id},
            )
            connection.execute(
                text("DELETE FROM users WHERE email = :email"), {"email": OTHER_USER_EMAIL}
            )

    clear()
    # The stub, because startup would otherwise build the real generator, which needs a key.
    app = create_app(with_api_key(settings), embedder=embedder, generator=StubGenerator())
    with TestClient(app, headers=AUTH_HEADERS) as test_client:
        yield test_client
    clear()


@pytest.fixture
def collection_id(client: TestClient) -> str:
    response = client.post("/collections", json={"name": "docs"})
    collection: str = response.json()["id"]
    return collection


def _upload(client: TestClient, collection_id: str, data: bytes, name: str = "report.pdf"):  # type: ignore[no-untyped-def]
    return client.post(
        "/documents",
        data={"collection_id": collection_id},
        files={"file": (name, data, "application/pdf")},
    )


def test_upload_returns_202_with_a_job(client: TestClient, collection_id: str) -> None:
    response = _upload(client, collection_id, MINIMAL_PDF)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert uuid.UUID(body["document_id"])
    assert uuid.UUID(body["job_id"])


def test_upload_creates_document_and_job_together(client: TestClient, collection_id: str) -> None:
    """The document and its queued work must be inserted in one transaction."""
    body = _upload(client, collection_id, MINIMAL_PDF).json()

    engine = build_engine(get_settings())
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT d.size_bytes, d.s3_key, j.status, j.attempts "
                "FROM documents d JOIN ingestion_jobs j ON j.document_id = d.id "
                "WHERE d.id = :document_id"
            ),
            {"document_id": body["document_id"]},
        ).one()

    assert row.size_bytes == len(MINIMAL_PDF)
    assert row.s3_key.startswith("documents/")
    assert row.status == "queued"
    assert row.attempts == 0


def test_unknown_collection_returns_404(client: TestClient) -> None:
    response = _upload(client, str(uuid.uuid4()), MINIMAL_PDF)

    assert response.status_code == 404


def other_users_collection() -> uuid.UUID:
    sessions = build_session_factory(build_engine(get_settings()))
    with sessions() as session:
        collection = Collection(user=User(email=OTHER_USER_EMAIL), name="theirs")
        session.add(collection)
        session.commit()
        return collection.id


def test_another_users_collection_is_indistinguishable_from_a_missing_one(
    client: TestClient,
) -> None:
    """Same status, same body, and nothing uploaded into someone else's collection."""
    theirs_id = other_users_collection()

    theirs = _upload(client, str(theirs_id), MINIMAL_PDF)
    missing = _upload(client, str(uuid.uuid4()), MINIMAL_PDF)

    assert (theirs.status_code, theirs.json()) == (missing.status_code, missing.json())
    assert theirs.status_code == 404
    with build_engine(get_settings()).connect() as connection:
        documents = connection.execute(
            text("SELECT count(*) FROM documents WHERE collection_id = :id"), {"id": theirs_id}
        ).scalar_one()
    assert documents == 0


def test_non_pdf_returns_400(client: TestClient, collection_id: str) -> None:
    response = _upload(client, collection_id, b"this is not a pdf", name="notes.txt")

    assert response.status_code == 400


def test_oversized_upload_returns_413(client: TestClient, collection_id: str) -> None:
    settings = get_settings()
    oversized = MINIMAL_PDF + b"0" * settings.max_upload_bytes

    response = _upload(client, collection_id, oversized)

    assert response.status_code == 413


def test_reuploading_identical_bytes_reuses_the_object(
    client: TestClient, collection_id: str
) -> None:
    """Keys are content hashes, so the same file twice is one object, two documents."""
    first = _upload(client, collection_id, MINIMAL_PDF).json()
    second = _upload(client, collection_id, MINIMAL_PDF, name="copy.pdf").json()

    assert first["document_id"] != second["document_id"]

    engine = build_engine(get_settings())
    with engine.connect() as connection:
        keys = (
            connection.execute(
                text("SELECT DISTINCT s3_key FROM documents WHERE id IN (:a, :b)"),
                {"a": first["document_id"], "b": second["document_id"]},
            )
            .scalars()
            .all()
        )

    assert len(keys) == 1
