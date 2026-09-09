"""Ingestion worker.

A second process beside the API, sharing its configuration and its database. The API
answers requests; this does the slow work those requests queue, so parsing and
embedding never occupy a request-handling process.

Claiming jobs arrives in the next commit. What exists here is the lifecycle around it:
configuration, database connectivity, the poll loop, and graceful shutdown.
"""

import logging
import signal
import threading
from types import FrameType

from app.config import Settings, get_settings
from app.db.session import build_engine, database_ok

logger = logging.getLogger(__name__)


def install_signal_handlers(stop: threading.Event) -> None:
    """Make termination signals request shutdown rather than kill the process.

    Kubernetes sends SIGTERM and waits before SIGKILL. Setting a flag the loop reads at
    its own boundary is what will let a worker finish or release the job it holds
    instead of dying part-way through writing results.
    """

    def handle(signum: int, frame: FrameType | None) -> None:
        logger.info("received %s, stopping after the current pass", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)


def run_worker(poll_seconds: float, stop: threading.Event) -> None:
    """Poll for work until asked to stop.

    Waiting on the stop event rather than sleeping means a termination signal is acted
    on immediately instead of after the remainder of the interval — a worker that slept
    through its interval would be killed before noticing SIGTERM once that interval
    exceeded the grace period.
    """
    logger.info("worker started, polling every %.1fs", poll_seconds)
    while not stop.is_set():
        # Claiming and running one job goes here.
        stop.wait(poll_seconds)
    logger.info("worker stopped")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings: Settings = get_settings()
    engine = build_engine(settings)
    # Reported, not fatal: the poll loop is the retry, and exiting here would crash-loop
    # a worker through a database restart it would otherwise ride out.
    logger.info("database %s", "reachable" if database_ok(engine) else "unreachable")

    stop = threading.Event()
    install_signal_handlers(stop)
    try:
        run_worker(settings.worker_poll_seconds, stop)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
