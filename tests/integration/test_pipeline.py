import logging
import time
import uuid
from collections.abc import Iterator, Sequence

import pymupdf
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.jobs import claim_job
from app.db.models import (
    EMBEDDING_DIM,
    TEXT_SEARCH_CONFIG,
    Chunk,
    Collection,
    Document,
    IngestionJob,
)
from app.db.session import build_engine, build_session_factory, database_ok
from app.ingest.chunk import chunk_pages
from app.ingest.parse import parse_pdf
from app.ingest.pipeline import UNEXPECTED_FAILURE, process_job
from app.services.embeddings import Embedder
from app.services.storage import ObjectStorage, build_storage, content_key
from app.worker import poll_once

WORDS = (
    "retrieval citation evidence answer question passage search index vector database "
    "worker queue ingestion chunk token overlap boundary heartbeat"
).split()


@pytest.fixture
def sessions() -> Iterator[sessionmaker[Session]]:
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
    yield build_session_factory(engine)
    clear()


@pytest.fixture
def storage() -> ObjectStorage:
    store = build_storage(get_settings())
    try:
        store.exists("probe")
    except Exception:
        pytest.skip("object storage unavailable; start it with `docker compose up -d minio`")
    return store


@pytest.fixture
def collection_id(sessions: sessionmaker[Session]) -> uuid.UUID:
    with sessions() as session:
        collection = Collection(user_id=get_settings().default_user_id, name="pipeline")
        session.add(collection)
        session.commit()
        return collection.id


def prose(words: int, seed: int) -> str:
    return " ".join(WORDS[(i * 7 + seed) % len(WORDS)] for i in range(words))


def make_pdf(pages: list[str]) -> bytes:
    """Build a PDF holding this text, one entry per page, set in short lines."""
    document = pymupdf.open()
    for content in pages:
        page = document.new_page()
        words = content.split()
        lines = [" ".join(words[i : i + 10]) for i in range(0, len(words), 10)]
        if lines:
            page.insert_text((72, 72), "\n".join(lines), fontsize=8)
    # pymupdf types tobytes() as Any; the call narrows it for warn_return_any.
    return bytes(document.tobytes())


def upload(
    sessions: sessionmaker[Session], storage: ObjectStorage, collection_id: uuid.UUID, data: bytes
) -> uuid.UUID:
    """Store the bytes and queue a job for them, the way POST /documents does."""
    key = content_key(data)
    storage.put(key, data, "application/pdf")
    with sessions() as session:
        document = Document(
            collection_id=collection_id,
            filename="report.pdf",
            content_type="application/pdf",
            size_bytes=len(data),
            s3_key=key,
        )
        job = IngestionJob(document=document, status="queued")
        session.add_all([document, job])
        session.commit()
        return job.id


def claim(sessions: sessionmaker[Session]) -> IngestionJob:
    settings = get_settings()
    with sessions() as session:
        job = claim_job(session, settings.job_stale_after, settings.job_max_attempts)
    assert job is not None
    return job


def job_state(sessions: sessionmaker[Session], job_id: uuid.UUID) -> tuple[str, str | None, int]:
    """Return a job's status, its error, and how many chunks its document has."""
    with sessions() as session:
        row = session.execute(
            text(
                "SELECT j.status, j.error, "
                "(SELECT count(*) FROM chunks c WHERE c.document_id = j.document_id) AS chunks "
                "FROM ingestion_jobs j WHERE j.id = :id"
            ),
            {"id": job_id},
        ).one()
        return row.status, row.error, row.chunks


class BrokenEmbedder:
    """The real tokenizer, with embedding that always raises: an error nobody planned for."""

    def __init__(self, real: Embedder) -> None:
        self.tokenizer = real.tokenizer

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("embedding backend unavailable")

    def embed_query(self, text: str) -> list[float]:
        raise RuntimeError("embedding backend unavailable")


class WorkerKilled(BaseException):
    """Stands in for SIGKILL or an out-of-memory kill: nothing in the worker can handle it."""


class DyingEmbedder:
    """The real tokenizer, in a worker that dies the moment it starts embedding.

    By then the job has been claimed, fetched, parsed, chunked, and heartbeated: the
    worker is genuinely partway through when it goes.
    """

    def __init__(self, real: Embedder) -> None:
        self.tokenizer = real.tokenizer

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        raise WorkerKilled

    def embed_query(self, text: str) -> list[float]:
        raise WorkerKilled


def quick_to_stall(seconds: int) -> Settings:
    """Settings whose staleness window passes in seconds rather than five minutes."""
    return get_settings().model_copy(update={"job_stale_after_seconds": seconds})


def wait_until_stale(settings: Settings) -> None:
    time.sleep(settings.job_stale_after_seconds + 0.25)


def test_a_queued_pdf_is_indexed_into_embedded_chunks(
    sessions: sessionmaker[Session],
    storage: ObjectStorage,
    embedder: Embedder,
    collection_id: uuid.UUID,
) -> None:
    job_id = upload(sessions, storage, collection_id, make_pdf([prose(300, n) for n in range(3)]))

    process_job(claim(sessions), sessions, storage, embedder)

    with sessions() as session:
        job = session.execute(
            text(
                "SELECT j.status, j.error, j.finished_at IS NOT NULL AS finished, "
                "d.page_count, d.id AS document_id "
                "FROM ingestion_jobs j JOIN documents d ON d.id = j.document_id "
                "WHERE j.id = :id"
            ),
            {"id": job_id},
        ).one()
        chunks = session.scalars(
            select(Chunk).where(Chunk.document_id == job.document_id).order_by(Chunk.chunk_index)
        ).all()

    assert (job.status, job.error, job.finished, job.page_count) == ("completed", None, True, 3)
    assert len(chunks) > 1
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert (chunks[0].page_start, chunks[-1].page_end) == (1, 3)
    assert all(chunk.collection_id == collection_id for chunk in chunks)
    assert all(len(chunk.embedding) == EMBEDDING_DIM for chunk in chunks)


def test_indexed_chunks_carry_the_full_text_vector_of_their_text(
    sessions: sessionmaker[Session],
    storage: ObjectStorage,
    embedder: Embedder,
    collection_id: uuid.UUID,
) -> None:
    """Full-text search reads `tsv`; a chunk without it could never be found by its words."""
    job_id = upload(sessions, storage, collection_id, make_pdf([prose(300, n) for n in range(2)]))

    process_job(claim(sessions), sessions, storage, embedder)

    assert job_state(sessions, job_id)[0] == "completed"
    with sessions() as session:
        chunks, differing = session.execute(
            select(
                func.count(),
                func.count().filter(
                    Chunk.tsv.is_distinct_from(func.to_tsvector(TEXT_SEARCH_CONFIG, Chunk.text))
                ),
            )
            .select_from(Chunk)
            .where(Chunk.collection_id == collection_id)
        ).one()
    assert chunks > 1
    assert differing == 0


def test_an_unreadable_file_fails_the_job_with_its_reason(
    sessions: sessionmaker[Session],
    storage: ObjectStorage,
    embedder: Embedder,
    collection_id: uuid.UUID,
) -> None:
    job_id = upload(sessions, storage, collection_id, b"not a pdf at all")

    process_job(claim(sessions), sessions, storage, embedder)

    status, error, chunks = job_state(sessions, job_id)
    assert (status, chunks) == ("failed", 0)
    assert error is not None and error.startswith("could not read pdf")


def test_a_pdf_without_text_fails_the_job(
    sessions: sessionmaker[Session],
    storage: ObjectStorage,
    embedder: Embedder,
    collection_id: uuid.UUID,
) -> None:
    job_id = upload(sessions, storage, collection_id, make_pdf(["", ""]))

    process_job(claim(sessions), sessions, storage, embedder)

    status, error, chunks = job_state(sessions, job_id)
    assert (status, chunks) == ("failed", 0)
    assert error is not None and "no extractable text" in error


def test_an_unexpected_error_fails_the_job_with_a_generic_reason_and_logs_the_detail(
    sessions: sessionmaker[Session],
    storage: ObjectStorage,
    embedder: Embedder,
    collection_id: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failure is terminal whatever the cause. The job, which callers read, gets a generic
    reason; the exception, which can carry internal detail, goes only to the log."""
    job_id = upload(sessions, storage, collection_id, make_pdf([prose(50, 1)]))

    with caplog.at_level(logging.ERROR, logger="app.ingest.pipeline"):
        process_job(claim(sessions), sessions, storage, BrokenEmbedder(embedder))

    assert job_state(sessions, job_id) == ("failed", UNEXPECTED_FAILURE, 0)
    [record] = [record for record in caplog.records if record.name == "app.ingest.pipeline"]
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
    assert str(record.exc_info[1]) == "embedding backend unavailable"
    assert str(job_id) in record.getMessage()


def test_a_worker_that_lost_its_claim_writes_nothing(
    sessions: sessionmaker[Session],
    storage: ObjectStorage,
    embedder: Embedder,
    collection_id: uuid.UUID,
) -> None:
    """A stalled worker, reclaimed while it slept, must not write over the new holder."""
    job_id = upload(sessions, storage, collection_id, make_pdf([prose(50, 2)]))
    stalled = claim(sessions)
    with sessions() as session:
        session.execute(
            text(
                "UPDATE ingestion_jobs SET heartbeat_at = now() - interval '10 minutes' "
                "WHERE id = :id"
            ),
            {"id": job_id},
        )
        session.commit()
    current = claim(sessions)
    assert current.attempts == stalled.attempts + 1

    process_job(stalled, sessions, storage, embedder)

    assert job_state(sessions, job_id) == ("running", None, 0)


def test_one_poll_claims_and_indexes_a_queued_upload(
    sessions: sessionmaker[Session],
    storage: ObjectStorage,
    embedder: Embedder,
    collection_id: uuid.UUID,
) -> None:
    job_id = upload(sessions, storage, collection_id, make_pdf([prose(50, 3)]))
    settings = get_settings()

    assert poll_once(settings, sessions, storage, embedder) is True
    assert job_state(sessions, job_id)[0] == "completed"
    assert poll_once(settings, sessions, storage, embedder) is False


def test_a_job_whose_worker_died_is_reclaimed_and_indexed_once(
    sessions: sessionmaker[Session],
    storage: ObjectStorage,
    embedder: Embedder,
    collection_id: uuid.UUID,
) -> None:
    """A worker killed mid-job leaves nothing behind, and its job is finished by another.

    The job is left alone while the dead worker's heartbeat is fresh, then reclaimed by
    the next poll once it goes stale, and the document is indexed exactly once.
    """
    data = make_pdf([prose(300, n) for n in range(2)])
    job_id = upload(sessions, storage, collection_id, data)
    settings = quick_to_stall(2)

    with pytest.raises(WorkerKilled):
        poll_once(settings, sessions, storage, DyingEmbedder(embedder))
    assert job_state(sessions, job_id) == ("running", None, 0)

    # The heartbeat is still fresh: a live worker must not take a job that may be in hand.
    assert poll_once(settings, sessions, storage, embedder) is False

    wait_until_stale(settings)
    assert poll_once(settings, sessions, storage, embedder) is True

    expected = len(chunk_pages(parse_pdf(data), embedder.tokenizer))
    with sessions() as session:
        row = session.execute(
            text(
                "SELECT j.attempts, (SELECT count(DISTINCT c.chunk_index) FROM chunks c "
                "WHERE c.document_id = j.document_id) AS distinct_chunks "
                "FROM ingestion_jobs j WHERE j.id = :id"
            ),
            {"id": job_id},
        ).one()
    assert job_state(sessions, job_id) == ("completed", None, expected)
    assert (row.attempts, row.distinct_chunks) == (2, expected)


def test_a_job_that_kills_every_worker_is_failed_once_its_attempts_run_out(
    sessions: sessionmaker[Session],
    storage: ObjectStorage,
    embedder: Embedder,
    collection_id: uuid.UUID,
) -> None:
    """Reclaim is bounded: after every attempt has died, the next poll fails the job."""
    job_id = upload(sessions, storage, collection_id, make_pdf([prose(50, 4)]))
    settings = quick_to_stall(1)

    for _ in range(settings.job_max_attempts):
        with pytest.raises(WorkerKilled):
            poll_once(settings, sessions, storage, DyingEmbedder(embedder))
        wait_until_stale(settings)

    assert poll_once(settings, sessions, storage, embedder) is False

    status, error, chunks = job_state(sessions, job_id)
    assert (status, chunks) == ("failed", 0)
    assert error is not None and f"{settings.job_max_attempts} attempts" in error
