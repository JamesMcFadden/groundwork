import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import get_settings
from app.db.models import Collection, Document, IngestionJob, User
from app.db.session import build_engine, build_session_factory, database_ok
from app.generation.stub import StubGenerator
from app.main import create_app
from app.services.embeddings import Embedder
from app.services.storage import build_storage

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
OTHER_USER_EMAIL = "someone-else@example.com"


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
            connection.execute(
                text("DELETE FROM users WHERE email = :email"), {"email": OTHER_USER_EMAIL}
            )

    clear()
    # The stub, because startup would otherwise build the real generator, which needs a key.
    app = create_app(settings, embedder=embedder, generator=StubGenerator())
    with TestClient(app) as test_client:
        yield test_client
    clear()


def insert_job(
    user_id: uuid.UUID, *, status: str = "queued", attempts: int = 0, error: str | None = None
) -> uuid.UUID:
    """Insert a collection, document, and job directly, without upload or storage."""
    sessions = build_session_factory(build_engine(get_settings()))
    now = datetime.now(UTC)
    with sessions() as session:
        collection = Collection(user_id=user_id, name=f"jobs-{uuid.uuid4().hex[:8]}")
        document = Document(
            collection=collection,
            filename="report.pdf",
            content_type="application/pdf",
            size_bytes=1,
            s3_key=f"documents/{uuid.uuid4().hex}",
        )
        job = IngestionJob(
            document=document,
            status=status,
            attempts=attempts,
            error=error,
            started_at=None if status == "queued" else now,
            finished_at=now if status in ("completed", "failed") else None,
        )
        session.add_all([collection, document, job])
        session.commit()
        return job.id


def other_user() -> uuid.UUID:
    sessions = build_session_factory(build_engine(get_settings()))
    with sessions() as session:
        user = User(email=OTHER_USER_EMAIL)
        session.add(user)
        session.commit()
        return user.id


def test_reports_a_queued_job(client: TestClient) -> None:
    job_id = insert_job(get_settings().default_user_id)

    response = client.get(f"/jobs/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(job_id)
    assert (body["status"], body["attempts"], body["error"]) == ("queued", 0, None)
    assert body["created_at"]
    assert (body["started_at"], body["finished_at"]) == (None, None)
    assert "heartbeat_at" not in body


def test_reports_why_a_job_failed(client: TestClient) -> None:
    reason = "no extractable text; scanned and image-only PDFs are out of scope"
    job_id = insert_job(get_settings().default_user_id, status="failed", attempts=1, error=reason)

    body = client.get(f"/jobs/{job_id}").json()

    assert (body["status"], body["attempts"], body["error"]) == ("failed", 1, reason)
    assert body["started_at"] and body["finished_at"]


def test_the_job_an_upload_returns_can_be_followed(client: TestClient) -> None:
    """The job id handed back by POST /documents is the one this endpoint reports."""
    try:
        build_storage(get_settings()).exists("probe")
    except Exception:
        pytest.skip("object storage unavailable; start it with `docker compose up -d minio`")
    collection = client.post("/collections", json={"name": "jobs"}).json()
    accepted = client.post(
        "/documents",
        data={"collection_id": collection["id"]},
        files={"file": ("report.pdf", MINIMAL_PDF, "application/pdf")},
    ).json()

    body = client.get(f"/jobs/{accepted['job_id']}").json()

    assert (body["id"], body["document_id"], body["status"]) == (
        accepted["job_id"],
        accepted["document_id"],
        "queued",
    )


def test_unknown_job_returns_404(client: TestClient) -> None:
    response = client.get(f"/jobs/{uuid.uuid4()}")

    assert response.status_code == 404


def test_another_users_job_is_indistinguishable_from_a_missing_one(client: TestClient) -> None:
    """Same status, same body: the response confirms nothing about someone else's job."""
    job_id = insert_job(other_user())

    theirs = client.get(f"/jobs/{job_id}")
    missing = client.get(f"/jobs/{uuid.uuid4()}")

    assert (theirs.status_code, theirs.json()) == (missing.status_code, missing.json())
    assert theirs.status_code == 404


def test_malformed_job_id_returns_422(client: TestClient) -> None:
    response = client.get("/jobs/not-a-uuid")

    assert response.status_code == 422
