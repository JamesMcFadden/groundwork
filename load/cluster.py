"""What seeding and running share: kubectl in the deployment's namespace, and the API."""

import subprocess
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

CONTEXT = "kind-groundwork"
NAMESPACE = "groundwork"
SECRETS = Path(__file__).parent.parent / "k8s" / "kind" / "secrets.env"


def kubectl(*args: str, stdin: str | None = None) -> str:
    """Run kubectl against the kind cluster's namespace and return what it printed."""
    result = subprocess.run(
        ["kubectl", "--context", CONTEXT, "-n", NAMESPACE, *args],
        input=stdin,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def api_key(secrets: Path = SECRETS) -> str:
    """The API key the cluster was given, read from the same gitignored file."""
    for line in secrets.read_text().splitlines():
        if line.startswith("API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"no API_KEY in {secrets}")


@contextmanager
def port_forward(target: str, port: int) -> Iterator[str]:
    """Forward a local port to the API, and yield its base URL once it answers."""
    process = subprocess.Popen(
        ["kubectl", "--context", CONTEXT, "-n", NAMESPACE, "port-forward", target, f"{port}:8000"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"{base}/health/live", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise SystemExit(f"port-forward to {target} never answered")
        yield base
    finally:
        process.kill()
