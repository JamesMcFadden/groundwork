import threading
import time

import pytest

from app.worker import run_worker


class ScriptedPoll:
    """A poll that plays back a script of outcomes, then stops the worker."""

    def __init__(self, stop: threading.Event, *outcomes: bool | Exception) -> None:
        self.stop = stop
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        if not self.outcomes:
            self.stop.set()
            return False
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def idle() -> bool:
    return False


def test_returns_without_polling_when_stop_is_already_set() -> None:
    stop = threading.Event()
    stop.set()
    poll = ScriptedPoll(stop)

    run_worker(poll, poll_seconds=60.0, stop=stop)

    assert poll.calls == 0


def test_a_long_poll_interval_does_not_delay_shutdown() -> None:
    """Shutdown interrupts the wait rather than waiting it out.

    This is the difference between waiting on the stop event and sleeping: a worker
    that slept through its interval would be killed before noticing SIGTERM once that
    interval exceeded the termination grace period.
    """
    stop = threading.Event()
    threading.Timer(0.05, stop.set).start()

    started = time.monotonic()
    run_worker(idle, poll_seconds=60.0, stop=stop)

    assert time.monotonic() - started < 1.0


def test_polls_again_without_waiting_while_there_is_work() -> None:
    """A backlog drains back to back; only an idle pass waits out the interval."""
    stop = threading.Event()
    poll = ScriptedPoll(stop, True, True, True)

    started = time.monotonic()
    run_worker(poll, poll_seconds=60.0, stop=stop)

    assert poll.calls == 4
    assert time.monotonic() - started < 1.0


def test_a_failing_poll_is_logged_and_survived(caplog: pytest.LogCaptureFixture) -> None:
    """A database restart should pause a worker, not kill it."""
    stop = threading.Event()
    poll = ScriptedPoll(stop, RuntimeError("database unavailable"))

    run_worker(poll, poll_seconds=0.01, stop=stop)

    assert poll.calls == 2
    assert "poll failed" in caplog.text


class Signals:
    """Counts the liveness signals a worker gives."""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> None:
        self.count += 1


def test_signals_liveness_before_every_poll_idle_or_busy() -> None:
    stop = threading.Event()
    poll = ScriptedPoll(stop, True, False, True)
    alive = Signals()

    run_worker(poll, poll_seconds=0.01, stop=stop, alive=alive)

    assert (poll.calls, alive.count) == (4, 4)


def test_signals_liveness_while_polls_fail() -> None:
    """A worker riding out a database outage is paused, not stuck.

    Were failed passes left unsignalled, the liveness probe would restart every worker
    during an outage: the failure the probe reads a file rather than the database to avoid.
    """
    stop = threading.Event()
    outage = RuntimeError("database unavailable")
    poll = ScriptedPoll(stop, outage, outage)
    alive = Signals()

    run_worker(poll, poll_seconds=0.01, stop=stop, alive=alive)

    assert (poll.calls, alive.count) == (3, 3)
