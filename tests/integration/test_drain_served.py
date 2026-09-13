"""Draining in the process `python -m app.main` starts, as the API's container runs it.

That command runs app/main.py as `__main__`, and uvicorn then imports `app.main` again as a
separate module, which an app built inside the test process never does. A draining flag
defined in app/main.py was two flags that way: SIGUSR1 set one and the app read the other.

Needs none of the Compose services, only the embedding model and port 8000, where the
served API listens.
"""

import http.client
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

PORT = 8000


def connection_header() -> str | None:
    """The `connection` header of one response, from a client that keeps connections open."""
    connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
    try:
        connection.request("GET", "/health/live")
        response = connection.getresponse()
        response.read()
        return response.getheader("connection")
    finally:
        connection.close()


def wait_until_serving(process: subprocess.Popen[bytes], log: Path) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = log.read_text()
            if "Could not load model" in output:
                pytest.skip("embedding model unavailable; it downloads once on first use (~63 MB)")
            pytest.fail(f"python -m app.main exited before serving:\n{output[-2000:]}")
        try:
            connection_header()
            return
        except OSError:
            time.sleep(0.5)
    pytest.fail(f"python -m app.main did not serve within 90 s:\n{log.read_text()[-2000:]}")


def test_sigusr1_to_the_served_process_closes_its_connections(tmp_path: Path) -> None:
    try:
        connection_header()
    except OSError:
        pass
    else:
        pytest.skip(f"port {PORT} is already serving; stop what holds it, such as Compose's api")

    log = tmp_path / "api.log"
    env = {**os.environ, "GENERATOR": "stub", "API_KEY": "drain-test-key"}
    with log.open("wb") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "app.main"], env=env, stdout=output, stderr=subprocess.STDOUT
        )
        try:
            wait_until_serving(process, log)
            before = connection_header()
            process.send_signal(signal.SIGUSR1)
            deadline = time.monotonic() + 5
            after = connection_header()
            while after != "close" and time.monotonic() < deadline:
                time.sleep(0.2)
                after = connection_header()
        finally:
            process.terminate()
            process.wait(timeout=30)

    assert (before, after) == (None, "close")
