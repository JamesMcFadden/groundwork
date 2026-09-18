"""The deployment measurement: one recorded run of the rule docs/roadmap.md fixes for it.

From this Mac, with nothing of this project built and no data kept:

    uv run python -m load.deployment --note "what this run is"

It clones this repository into an empty directory, copies .env.example to .env, and runs the
one command the README documents, timing it to a ready API. It then uploads a document and
asks a question, so what is recorded is a service that answers rather than one that merely
starts.

It refuses to run warm. The criterion is about a fresh environment, and a machine that has
built this project before answers in seconds for reasons a new reader would never see: the
application images must be absent, the two Compose volumes must be gone, and the build cache
must be empty. Base images may stay in the local store, as the rule allows: pulling them
measures this network rather than the project. It refuses to record from code that differs
from HEAD. A failed run is recorded, never rerun in its place.
"""

import argparse
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eval.corpus import load_corpus
from eval.golden import AnswerableQuestion, GoldenSet, load_golden
from load.seed import multipart

BASE = "http://localhost:8000"
DOCUMENT = "uam-risk.pdf"
REPOSITORY = Path(__file__).parent.parent
RESULTS_DIR = Path(__file__).parent / "results"
CODE = ("load/deployment.py", "load/seed.py")
# What the run must find absent, since each would let a step be skipped.
IMAGES = ("groundwork-api", "groundwork-worker")
VOLUMES = ("groundwork_pgdata", "groundwork_miniodata")
READY_TIMEOUT_SECONDS = 900
JOB_TIMEOUT_SECONDS = 600


def run(command: list[str], cwd: Path | None = None) -> dict[str, Any]:
    """Run a command, timing it, and record what it was and what it did."""
    started = time.monotonic()
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    return {
        "command": " ".join(command),
        "seconds": round(time.monotonic() - started, 2),
        "returncode": result.returncode,
        "stdout": result.stdout[-2000:],
        "stderr": result.stderr[-2000:],
    }


def docker(*args: str) -> str:
    """Ask docker something, and return what it printed."""
    result = subprocess.run(["docker", *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def build_cache_records() -> int:
    """How many cache records the default builder holds.

    `docker buildx du --format json` prints one object per record and no total, and its
    sizes are human strings; the count is what matters here, since a cold build must start
    with none, and counting avoids parsing "4.096kB" back into a number.
    """
    return len([line for line in docker("buildx", "du", "--format", "json").splitlines() if line])


def warm_problem() -> str | None:
    """Why this Mac cannot give a cold result, or None when it can."""
    built = [image for image in IMAGES if docker("image", "ls", "-q", image)]
    if built:
        return f"already built: {', '.join(built)}; a cold run must build from source"
    kept = [
        volume for volume in VOLUMES if docker("volume", "ls", "-q", "--filter", f"name=^{volume}$")
    ]
    if kept:
        return f"still present: {', '.join(kept)}; the clone would adopt these, not start empty"
    cache = build_cache_records()
    if cache:
        return f"the build cache holds {cache} records; a cold build must start with none"
    return None


def choose_question(golden: GoldenSet, document: str) -> AnswerableQuestion:
    """The first answerable question, in file order, whose evidence is in `document`."""
    for question in golden.answerable:
        if any(evidence.document == document for evidence in question.evidence):
            return question
    raise SystemExit(f"no answerable question has evidence in {document}")


def cites(answer: dict[str, Any], document: str) -> bool:
    return any(citation.get("filename") == document for citation in answer.get("citations", []))


def verdict(checks: dict[str, dict[str, Any]]) -> str:
    return "met" if checks and all(check["passed"] for check in checks.values()) else "not met"


def call(
    method: str,
    path: str,
    key: str | None = None,
    body: bytes | None = None,
    content_type: str = "application/json",
) -> tuple[int, Any]:
    """A request to the API on localhost; status 0 when no response came back."""
    headers = {"content-type": content_type}
    if key:
        headers["x-api-key"] = key
    request = urllib.request.Request(f"{BASE}{path}", body, headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(errors="replace")[:200]
    except OSError as error:
        return 0, repr(error)


def wait_until_ready() -> float | None:
    """Seconds until /health/ready answers 200, or None if it never did."""
    started = time.monotonic()
    deadline = started + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status, _ = call("GET", "/health/ready")
        if status == 200:
            return round(time.monotonic() - started, 2)
        time.sleep(1)
    return None


def wait_for_job(key: str, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    while True:
        status, job = call("GET", f"/jobs/{job_id}", key)
        if status == 200 and job["status"] in ("completed", "failed"):
            finished: dict[str, Any] = job
            return finished
        if time.monotonic() > deadline:
            return {"status": "unfinished", "after_seconds": JOB_TIMEOUT_SECONDS, "last": job}
        time.sleep(2)


def documented_steps(clone: Path) -> list[dict[str, Any]]:
    """The README's setup, unchanged: copy the example environment, then one compose up."""
    shutil.copyfile(clone / ".env.example", clone / ".env")
    return [run(["docker", "compose", "up", "-d", "--build"], cwd=clone)]


def api_key(clone: Path) -> str:
    """The key the copied .env gives, which the README tells a reader to send."""
    for line in (clone / ".env").read_text().splitlines():
        if line.startswith("API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no API_KEY in the copied .env")


def run_checks(
    clone: Path, question: AnswerableQuestion, pdf: bytes
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    """The rule's checks, in the order a reader would meet them."""
    checks: dict[str, dict[str, Any]] = {}
    commands = documented_steps(clone)
    checks["documented_steps_succeed"] = {
        "passed": all(command["returncode"] == 0 for command in commands),
        "commands": [command["command"] for command in commands],
        "seconds": sum(command["seconds"] for command in commands),
    }

    ready = wait_until_ready()
    checks["api_becomes_ready"] = {"passed": ready is not None, "seconds": ready}
    if ready is None:
        return checks, commands, None

    key = api_key(clone)
    status, collection = call("POST", "/collections", key, json.dumps({"name": "fresh"}).encode())
    checks["collection_created"] = {"passed": status == 201, "status": status}
    if status != 201:
        return checks, commands, None

    body, content_type = multipart({"collection_id": collection["id"]}, DOCUMENT, pdf)
    status, upload = call("POST", "/documents", key, body, content_type)
    checks["document_accepted"] = {"passed": status == 202, "status": status}
    if status != 202:
        return checks, commands, None

    job = wait_for_job(key, upload["job_id"])
    checks["document_indexed"] = {"passed": job["status"] == "completed", "job": job["status"]}

    payload = {"collection_id": collection["id"], "question": question.question}
    status, answer = call("POST", "/questions", key, json.dumps(payload).encode())
    answered = status == 201 and isinstance(answer, dict) and answer.get("outcome") == "answered"
    checks["question_answered"] = {
        "passed": bool(answered) and cites(answer, DOCUMENT),
        "status": status,
        "outcome": answer.get("outcome") if isinstance(answer, dict) else answer,
        "question_id": question.id,
    }
    return checks, commands, job


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--note", default="", help="recorded with the run")
    parser.add_argument(
        "--clone-into",
        type=Path,
        required=True,
        help="an empty directory to clone into; kept afterwards, so the run can be inspected",
    )
    arguments = parser.parse_args()

    git = ["git", "status", "--porcelain", "--", *CODE]
    if subprocess.run(git, check=True, capture_output=True, text=True).stdout:
        raise SystemExit("refusing to record: the measurement's code differs from HEAD")
    clone = arguments.clone_into
    if clone.exists() and any(clone.iterdir()):
        raise SystemExit(f"refusing to run: {clone} is not empty")
    problem = warm_problem()
    if problem:
        raise SystemExit(f"refusing to run: {problem}")

    corpus = load_corpus()
    golden = load_golden([document.filename for document in corpus.documents])
    question = choose_question(golden, DOCUMENT)
    pdf = next(d for d in corpus.documents if d.filename == DOCUMENT).path.read_bytes()
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()

    started = datetime.now(UTC)
    clone_command = run(["git", "clone", str(REPOSITORY), str(clone)])
    if clone_command["returncode"] != 0:
        raise SystemExit(f"the clone failed: {clone_command['stderr']}")

    checks, commands, job = run_checks(clone, question, pdf)
    record = {
        "label": "deployment",
        "rule": "docs/roadmap.md, M8, pre-registered rule Deployment",
        "note": arguments.note,
        "started_at": started.isoformat(),
        "ended_at": datetime.now(UTC).isoformat(),
        "revision": revision,
        "clone": str(clone),
        # What "cold" meant for this run, so the time is read for what it is.
        "cold": {
            "images_absent": list(IMAGES),
            "volumes_absent": list(VOLUMES),
            "build_cache_records": 0,
            "base_images": "left in the local store, as the rule allows",
        },
        "document": DOCUMENT,
        "golden_sha256": golden.sha256,
        "commands": [clone_command, *commands],
        "checks": checks,
        "seconds_to_ready": checks.get("api_becomes_ready", {}).get("seconds"),
        "ingestion_job": job,
        "verdict": verdict(checks),
    }
    path = RESULTS_DIR / f"{started:%Y%m%dT%H%M%SZ}-deployment.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    for name, check in checks.items():
        print(f"{'pass' if check['passed'] else 'FAIL'}  {name}")
    print(f"verdict: {record['verdict']}; recorded in {path.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
