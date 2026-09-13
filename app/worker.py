"""Ingestion worker.

A second process beside the API, sharing its configuration and its database. The API
answers requests; this does the slow work those requests queue, so parsing and
embedding never occupy a request-handling process.
"""

import logging
import signal
import threading
from collections.abc import Callable
from functools import partial
from pathlib import Path
from types import FrameType

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.jobs import claim_job, expire_exhausted
from app.db.session import build_engine, build_session_factory, database_ok
from app.ingest.pipeline import process_job
from app.logs import configure_logging
from app.services.embeddings import Embedder, FastEmbedder
from app.services.storage import ObjectStorage, build_storage

logger = logging.getLogger(__name__)


def install_signal_handlers(stop: threading.Event) -> None:
    """Make termination signals request shutdown rather than kill the process.

    Kubernetes sends SIGTERM and waits before SIGKILL. Setting a flag the loop reads
    between jobs lets a worker finish the job it holds instead of dying part-way through
    writing results.
    """

    def handle(signum: int, frame: FrameType | None) -> None:
        logger.info("received %s, stopping after the current pass", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)


def liveness_signal(path: Path | None) -> Callable[[], None]:
    """Return what the worker calls to show it is still making progress.

    The worker serves no HTTP, so its liveness probe reads how recently this file was
    touched. The file says nothing about the database on purpose: `heartbeat_at` is fresh
    only while a job runs, so an idle worker would look stuck, and a probe that needed the
    database would restart every worker during an outage. Unset, nothing is written.
    """
    if path is None:
        return lambda: None
    return path.touch


def run_worker(
    poll: Callable[[], bool],
    poll_seconds: float,
    stop: threading.Event,
    alive: Callable[[], None] = lambda: None,
) -> None:
    """Call `poll` until asked to stop, waiting between calls only when it found no work.

    `poll` reports whether it did anything, so a backlog drains back to back and only an
    idle pass waits. An exception is logged and survived: a database restart should
    pause a worker, not kill it.

    Waiting on the stop event rather than sleeping means a termination signal is acted
    on immediately instead of after the remainder of the interval — a worker that slept
    through its interval would be killed before noticing SIGTERM once that interval
    exceeded the grace period.

    `alive` is called at the start of every pass, a failed one included, so a worker riding
    out a database outage never looks stuck to its liveness probe.
    """
    logger.info("worker started, polling every %.1fs", poll_seconds)
    while not stop.is_set():
        alive()
        try:
            worked = poll()
        except Exception:
            logger.exception("poll failed; trying again in %.1fs", poll_seconds)
            worked = False
        if not worked:
            stop.wait(poll_seconds)
    logger.info("worker stopped")


def poll_once(
    settings: Settings,
    sessions: sessionmaker[Session],
    storage: ObjectStorage,
    embedder: Embedder,
    alive: Callable[[], None] = lambda: None,
) -> bool:
    """Expire abandoned jobs, then claim and process one. Return whether one was claimed."""
    with sessions() as session:
        expired = expire_exhausted(session, settings.job_stale_after, settings.job_max_attempts)
        job = claim_job(session, settings.job_stale_after, settings.job_max_attempts)
    if expired:
        logger.warning("expired %d abandoned job(s) with no attempts left", expired)
    if job is None:
        return False
    logger.info("claimed job %s, attempt %d", job.id, job.attempts)
    process_job(job, sessions, storage, embedder, alive)
    return True


def main() -> None:
    configure_logging()
    settings = get_settings()
    engine = build_engine(settings)
    # Reported, not fatal: the poll loop is the retry, and exiting here would crash-loop
    # a worker through a database restart it would otherwise ride out.
    logger.info("database %s", "reachable" if database_ok(engine) else "unreachable")

    # Loaded before the first claim, so missing weights stop the worker at startup
    # rather than failing the first job that needs them.
    embedder = FastEmbedder(
        cache_dir=settings.embedding_cache_dir, threads=settings.embedding_threads
    )
    alive = liveness_signal(settings.worker_liveness_file)
    poll = partial(
        poll_once,
        settings,
        build_session_factory(engine),
        build_storage(settings),
        embedder,
        alive,
    )

    stop = threading.Event()
    install_signal_handlers(stop)
    try:
        run_worker(poll, settings.worker_poll_seconds, stop, alive)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
