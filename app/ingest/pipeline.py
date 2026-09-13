"""One ingestion job, from a stored PDF to embedded chunks.

A job ends one of three ways. It completes, with its chunks written in the same
transaction that marks it done. It fails, with the reason recorded. Or this worker finds
it no longer holds the job — reclaimed by another worker after a stall, or deleted with
its document — and walks away without writing anything.
"""

import logging

from sqlalchemy import delete, update
from sqlalchemy.orm import Session, sessionmaker

from app.db.jobs import complete_job, fail_job, heartbeat
from app.db.models import Chunk, Document, IngestionJob
from app.ingest.chunk import chunk_pages
from app.ingest.parse import ParseError, parse_pdf
from app.services.embeddings import Embedder
from app.services.storage import ObjectStorage

logger = logging.getLogger(__name__)

# Heartbeats fall between batches. Thirty-two full chunks embed in about two seconds on
# a laptop CPU, far inside the five minutes after which a job counts as abandoned.
EMBED_BATCH = 32

# The reason recorded for a failure this code did not anticipate, and so what
# `GET /jobs/{job_id}` reports. The exception itself can carry a storage error's or a
# library's internal detail, so it goes only to the log, with its traceback.
UNEXPECTED_FAILURE = "unexpected error during ingestion"


class ClaimLost(Exception):
    """This worker no longer holds the job, so nothing it computed may be written."""


def process_job(
    job: IngestionJob,
    sessions: sessionmaker[Session],
    storage: ObjectStorage,
    embedder: Embedder,
) -> None:
    """Take a claimed job to its end: completed, failed, or left to whoever holds it now.

    Every failure is terminal. A document that cannot be parsed never will be, and its
    reason is recorded as it is. Anything else is logged with its traceback and recorded
    under a generic reason, rather than retried; only a worker that dies
    outright has its job retried, through reclaim. If recording the failure itself
    fails because the database is down, the exception propagates, the job stays
    running, and reclaim picks it up once the heartbeat goes stale — as after a crash.
    """
    try:
        _ingest(job, sessions, storage, embedder)
    except ClaimLost:
        logger.warning("job %s is held elsewhere now; discarding attempt %d", job.id, job.attempts)
    except ParseError as exc:
        logger.info("job %s failed: %s", job.id, exc)
        _fail(job, sessions, str(exc))
    except Exception:
        logger.exception("job %s failed unexpectedly", job.id)
        _fail(job, sessions, UNEXPECTED_FAILURE)
    else:
        logger.info("job %s completed", job.id)


def _ingest(
    job: IngestionJob,
    sessions: sessionmaker[Session],
    storage: ObjectStorage,
    embedder: Embedder,
) -> None:
    # Read what the job needs and release the connection: nothing below holds one while
    # the slow work runs.
    with sessions() as session:
        document = session.get(Document, job.document_id)
        if document is None:
            raise ClaimLost
        key, collection_id = document.s3_key, document.collection_id

    pages = parse_pdf(storage.get(key))
    chunks = chunk_pages(pages, embedder.tokenizer)

    vectors: list[list[float]] = []
    for offset in range(0, len(chunks), EMBED_BATCH):
        _heartbeat(job, sessions)
        batch = chunks[offset : offset + EMBED_BATCH]
        vectors.extend(embedder.embed_passages([chunk.text for chunk in batch]))

    with sessions() as session:
        # Completion goes first: it confirms the claim and locks the job row before any
        # chunk is written. Leaving the block without committing rolls everything back.
        if not complete_job(session, job.id, job.attempts):
            raise ClaimLost
        # A reindexed document's old chunks go in the transaction that writes the new ones,
        # so search sees one set or the other, never both or neither. Past questions keep
        # their retrieval results, which lose only the chunk id.
        session.execute(
            delete(Chunk).where(Chunk.document_id == job.document_id),
            execution_options={"synchronize_session": False},
        )
        session.add_all(
            Chunk(
                document_id=job.document_id,
                collection_id=collection_id,
                chunk_index=chunk.index,
                text=chunk.text,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                token_count=chunk.token_count,
                embedding=vector,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        )
        session.execute(
            update(Document).where(Document.id == job.document_id).values(page_count=len(pages))
        )
        session.commit()


def _heartbeat(job: IngestionJob, sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        if not heartbeat(session, job.id, job.attempts):
            raise ClaimLost


def _fail(job: IngestionJob, sessions: sessionmaker[Session], error: str) -> None:
    with sessions() as session:
        if not fail_job(session, job.id, job.attempts, error):
            logger.warning("job %s failed but is held elsewhere now; not recording it", job.id)
