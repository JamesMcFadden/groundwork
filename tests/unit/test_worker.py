import threading
import time

from app.worker import run_worker


def test_returns_without_polling_when_stop_is_already_set() -> None:
    stop = threading.Event()
    stop.set()

    started = time.monotonic()
    run_worker(poll_seconds=60.0, stop=stop)

    assert time.monotonic() - started < 1.0


def test_a_long_poll_interval_does_not_delay_shutdown() -> None:
    """Shutdown interrupts the wait rather than waiting it out.

    This is the difference between waiting on the stop event and sleeping: a worker
    that slept through its interval would be killed before noticing SIGTERM once that
    interval exceeded the termination grace period.
    """
    stop = threading.Event()
    threading.Timer(0.05, stop.set).start()

    started = time.monotonic()
    run_worker(poll_seconds=60.0, stop=stop)

    assert time.monotonic() - started < 1.0
