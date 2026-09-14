"""The AWS smoke test: one recorded run of the rule docs/roadmap.md fixes for the AWS criterion.

From this Mac against https://api.groundworkproj.com, once the service is deployed:

    uv run python -m load.smoke --note "the network it ran over"

It refuses to run unless this Mac resolves the host to the addresses public DNS gives, so a
stale cache cannot point it at some other server, and on a network that accepts connections
on port 80 to an address where nothing can listen, which would make a closed port look open.
It refuses to record from code that differs from HEAD. It then runs every check the rule
names, writes each to load/results/, and prints the verdict. A failed check is recorded,
never rerun in its place.
"""

import argparse
import json
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from typing import IO, Any

from eval.corpus import load_corpus
from eval.golden import AnswerableQuestion, GoldenSet, load_golden
from infra.deploy_env import OVERLAY, aws, read_env
from load.cluster import api_key
from load.seed import multipart

HOST = "api.groundworkproj.com"
DOCUMENT = "uam-risk.pdf"
RESULTS_DIR = Path(__file__).parent / "results"
JOB_TIMEOUT_SECONDS = 600
CODE = ("load/smoke.py", "load/seed.py", "load/cluster.py", "infra/deploy_env.py")
# Reserved for documentation (RFC 5737) and never routed, so nothing can accept a connection
# there: a network that lets one through intercepts port 80, as a mobile carrier did for the
# first recorded run.
CONTROL_ADDRESS = "192.0.2.1"


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


def ingestion_seconds(job: dict[str, Any]) -> dict[str, float | None]:
    """From upload to indexed, and the worker's own part of it, from the job's timestamps."""

    def between(start: str | None, end: str | None) -> float | None:
        if not start or not end:
            return None
        return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()

    return {
        "upload_to_indexed": between(job.get("created_at"), job.get("finished_at")),
        "worker": between(job.get("started_at"), job.get("finished_at")),
    }


def local_addresses(host: str) -> list[str]:
    return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, 443, socket.AF_INET)})


def public_addresses(host: str) -> list[str]:
    """The host's addresses as Google's resolver gives them, over HTTPS rather than port 53."""
    url = f"https://dns.google/resolve?name={host}&type=A"
    with urllib.request.urlopen(url, timeout=10) as response:
        answers = json.loads(response.read()).get("Answer", [])
    return sorted(answer["data"] for answer in answers if answer["type"] == 1)


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect is a status to record, not something to follow to another server."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


OPENER = urllib.request.build_opener(_NoRedirects)


def call(
    method: str,
    path: str,
    key: str | None = None,
    body: bytes | None = None,
    content_type: str = "application/json",
) -> tuple[int, Any]:
    """A request to the API over HTTPS, verified with the system's trust store; status 0 when
    no response came back."""
    headers = {"content-type": content_type}
    if key:
        headers["x-api-key"] = key
    request = urllib.request.Request(f"https://{HOST}{path}", body, headers, method=method)
    try:
        with OPENER.open(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(errors="replace")[:200]
    except OSError as error:
        return 0, repr(error)


def certificate(host: str) -> dict[str, Any]:
    """The TLS session a client gets: the system's trust store, and the name checked."""
    context = ssl.create_default_context()
    with (
        socket.create_connection((host, 443), timeout=10) as raw,
        context.wrap_socket(raw, server_hostname=host) as tls,
    ):
        peer: Any = tls.getpeercert()
        return {
            "tls_version": tls.version(),
            "subject": {key: value for rdn in peer["subject"] for key, value in rdn},
            "issuer": {key: value for rdn in peer["issuer"] for key, value in rdn},
            "not_after": peer["notAfter"],
        }


def connection(address: str, port: int) -> str:
    """`open` if a TCP connection is accepted within 5 s, or the name of the error instead."""
    try:
        with socket.create_connection((address, port), timeout=5):
            return "open"
    except OSError as error:
        return type(error).__name__


def port_80(addresses: list[str]) -> dict[str, str]:
    """Each address's response to a connection on port 80."""
    return {address: connection(address, 80) for address in addresses}


def precheck_problem(local: list[str], public: list[str], control: str) -> str | None:
    """Why this Mac's network cannot give a true result, or None when it can.

    `control` is what a connection on port 80 to CONTROL_ADDRESS returned.
    """
    if not public or local != public:
        return f"this Mac resolves {HOST} to {local}, public DNS to {public}"
    if control == "open":
        return (
            f"this network accepts connections on port 80 to {CONTROL_ADDRESS}, where nothing "
            "can listen, so it would report the load balancer's port 80 open whatever it is"
        )
    return None


def wait_for_job(key: str, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    while True:
        status, job = call("GET", f"/jobs/{job_id}", key)
        if status == 200 and job["status"] in ("completed", "failed"):
            job_record: dict[str, Any] = job
            return job_record
        if time.monotonic() > deadline:
            return {"status": "unfinished", "after_seconds": JOB_TIMEOUT_SECONDS, "last": job}
        time.sleep(2)


def deployed_images() -> dict[str, Any]:
    """The images the overlay last applied, from deploy.env, with the commit their tag names."""
    deploy = read_env(OVERLAY / "deploy.env")
    images: dict[str, Any] = {"api": deploy.get("API_IMAGE"), "worker": deploy.get("WORKER_IMAGE")}
    try:
        repository, _, digest = str(images["api"]).rpartition("@")
        found = aws(
            "ecr",
            "describe-images",
            "--repository-name",
            repository.rsplit("/", 1)[-1],
            "--image-ids",
            f"imageDigest={digest}",
        )
        images["revision"] = (found["imageDetails"][0].get("imageTags") or [None])[0]
    except (subprocess.CalledProcessError, KeyError, IndexError) as error:
        images["revision_error"] = repr(error)
    return images


def run_checks(key: str, question: AnswerableQuestion, pdf: bytes, addresses: list[str]) -> Any:
    checks: dict[str, dict[str, Any]] = {}
    try:
        checks["certificate"] = {"passed": True, **certificate(HOST)}
    except OSError as error:
        checks["certificate"] = {"passed": False, "error": repr(error)}

    status, _ = call("GET", "/health/ready")
    checks["health_ready"] = {"passed": status == 200, "status": status}

    status, _ = call("POST", "/collections", body=b'{"name": "without-a-key"}')
    checks["no_key_rejected"] = {"passed": status == 401, "status": status}

    ports = port_80(addresses)
    checks["port_80_closed"] = {"passed": "open" not in ports.values(), "addresses": ports}

    name = f"aws-smoke-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    status, collection = call("POST", "/collections", key, json.dumps({"name": name}).encode())
    collection_id = collection.get("id") if status == 201 and isinstance(collection, dict) else None
    upload_status, job = 0, None
    if collection_id:
        body, content_type = multipart({"collection_id": collection_id}, DOCUMENT, pdf)
        upload_status, upload = call("POST", "/documents", key, body, content_type)
        if upload_status == 202 and isinstance(upload, dict):
            job = wait_for_job(key, upload["job_id"])
    checks["document_ingested"] = {
        "passed": upload_status == 202 and job is not None and job["status"] == "completed",
        "collection_status": status,
        "upload_status": upload_status,
        "job": job,
    }

    if not checks["document_ingested"]["passed"]:
        checks["question_answered"] = {"passed": False, "not_run": "the document was not ingested"}
        return checks, job
    payload = {"collection_id": collection_id, "question": question.question}
    status, answer = call("POST", "/questions", key, json.dumps(payload).encode())
    answer = answer if isinstance(answer, dict) else {"body": answer}
    checks["question_answered"] = {
        "passed": status == 201 and answer.get("outcome") == "answered" and cites(answer, DOCUMENT),
        "status": status,
        "question_id": question.id,
        "outcome": answer.get("outcome"),
        "citations": [
            {key: citation.get(key) for key in ("marker", "filename", "page_start", "page_end")}
            for citation in answer.get("citations", [])
        ],
        "timings": answer.get("timings"),
    }
    return checks, job


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--note", default="", help="recorded with the run")
    note = parser.parse_args().note

    git = ["git", "status", "--porcelain", "--", *CODE]
    if subprocess.run(git, check=True, capture_output=True, text=True).stdout:
        raise SystemExit("refusing to record: the smoke test's code differs from HEAD")
    local, public = local_addresses(HOST), public_addresses(HOST)
    control = connection(CONTROL_ADDRESS, 80)
    problem = precheck_problem(local, public, control)
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
    checks, job = run_checks(api_key(OVERLAY / "secrets.env"), question, pdf, public)
    record = {
        "label": "aws-smoke",
        "rule": "docs/roadmap.md, M7, pre-registered rule AWS",
        "note": note,
        "started_at": started.isoformat(),
        "ended_at": datetime.now(UTC).isoformat(),
        "host": HOST,
        "addresses": {"this_mac": local, "public_dns": public},
        "port_80_control": {"address": CONTROL_ADDRESS, "result": control},
        "smoke_test_revision": revision,
        "images": deployed_images(),
        "golden_sha256": golden.sha256,
        "document": DOCUMENT,
        "checks": checks,
        "ingestion_seconds": ingestion_seconds(job) if job else None,
        "verdict": verdict(checks),
    }
    path = RESULTS_DIR / f"{started:%Y%m%dT%H%M%SZ}-aws-smoke.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    for name, check in checks.items():
        print(f"{'pass' if check['passed'] else 'FAIL'}  {name}")
    print(f"verdict: {record['verdict']}; recorded in {path.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
