"""Draining: once asked, the API closes each connection after its response."""

import os
import signal
import threading

from fastapi.testclient import TestClient

from app.main import create_app, install_drain_signal
from app.middleware import DRAINING


def test_responses_keep_their_connection_until_draining() -> None:
    response = TestClient(create_app(draining=threading.Event())).get("/health/live")

    assert "connection" not in response.headers


def test_every_response_closes_its_connection_once_draining() -> None:
    """Whatever the route, a 404 included: every client must reconnect elsewhere."""
    draining = threading.Event()
    client = TestClient(create_app(draining=draining))

    draining.set()
    responses = [client.get("/health/live"), client.get("/nowhere")]

    assert [(r.status_code, r.headers.get("connection")) for r in responses] == [
        (200, "close"),
        (404, "close"),
    ]


def test_an_app_built_without_a_flag_follows_the_process_draining_flag() -> None:
    """The flag SIGUSR1 sets is the one the served app reads."""
    client = TestClient(create_app())
    try:
        DRAINING.set()
        response = client.get("/health/live")
    finally:
        DRAINING.clear()

    assert response.headers.get("connection") == "close"


def test_sigusr1_starts_draining() -> None:
    """Kubernetes' preStop hook sends SIGUSR1 before SIGTERM; nothing else starts draining."""
    draining = threading.Event()
    previous = signal.getsignal(signal.SIGUSR1)
    try:
        install_drain_signal(draining)
        os.kill(os.getpid(), signal.SIGUSR1)
        started = draining.wait(timeout=5)
    finally:
        signal.signal(signal.SIGUSR1, previous)

    assert started
