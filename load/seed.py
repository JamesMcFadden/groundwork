"""Seed the kind deployment with the evaluation corpus and the golden set's questions.

    uv run python -m load.seed

Uploads the frozen corpus to a new collection through the API, so the worker in the cluster
indexes it, and waits for every job. Then writes the collection's id and the golden set's
38 questions, as eval/golden.toml words them, into the `load-input` ConfigMap the k6 script
reads. The questions are only load here: nothing is scored.
"""

import json
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from eval.corpus import load_corpus
from eval.golden import load_golden
from load.cluster import api_key, kubectl, port_forward

CONFIGMAP = "load-input"
LOCAL_PORT = 18080
JOB_TIMEOUT_SECONDS = 600


def request(
    base: str, key: str, method: str, path: str, body: bytes | None = None, content_type: str = ""
) -> Any:
    headers = {"x-api-key": key, "content-type": content_type or "application/json"}
    call = urllib.request.Request(f"{base}{path}", data=body, method=method, headers=headers)
    with urllib.request.urlopen(call, timeout=60) as response:
        return json.loads(response.read())


def multipart(fields: dict[str, str], filename: str, data: bytes) -> tuple[bytes, str]:
    """A multipart/form-data body holding text fields and one PDF, and its content type."""
    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, value in fields.items()
    ]
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/pdf\r\n\r\n".encode()
        + data
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def wait_for_jobs(base: str, key: str, job_ids: list[str]) -> list[dict[str, Any]]:
    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    pending, finished = set(job_ids), []
    while pending:
        if time.monotonic() > deadline:
            raise SystemExit(f"{len(pending)} job(s) unfinished after {JOB_TIMEOUT_SECONDS} s")
        for job_id in sorted(pending):
            job = request(base, key, "GET", f"/jobs/{job_id}")
            if job["status"] in ("completed", "failed"):
                finished.append(job)
                pending.discard(job_id)
        time.sleep(2)
    return finished


def main() -> None:
    corpus = load_corpus()
    golden = load_golden([document.filename for document in corpus.documents])
    questions = [q.question for q in golden.answerable] + [q.question for q in golden.unanswerable]
    key = api_key()

    # The worker ingests the uploads; runs scale it back to 0.
    kubectl("scale", "deployment/worker", "--replicas=1")
    kubectl("rollout", "status", "deployment/worker", "--timeout=300s")

    with port_forward("svc/api", LOCAL_PORT) as base:
        name = f"load-corpus-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        collection = request(base, key, "POST", "/collections", json.dumps({"name": name}).encode())
        job_ids = []
        for document in corpus.documents:
            body, content_type = multipart(
                {"collection_id": collection["id"]}, document.filename, document.path.read_bytes()
            )
            job_ids.append(request(base, key, "POST", "/documents", body, content_type)["job_id"])
            print(f"uploaded {document.filename}")
        jobs = wait_for_jobs(base, key, job_ids)

    failed = [(job["id"], job["error"]) for job in jobs if job["status"] != "completed"]
    if failed:
        raise SystemExit(f"ingestion failed: {failed}")

    payload = {
        "collection_id": collection["id"],
        "collection_name": name,
        "corpus_manifest_sha256": corpus.manifest_sha256,
        "questions": questions,
    }
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "input.json"
        path.write_text(json.dumps(payload, indent=2))
        manifest = kubectl(
            "create", "configmap", CONFIGMAP, f"--from-file=input.json={path}",
            "--dry-run=client", "-o", "yaml",
        )  # fmt: skip
    kubectl("apply", "-f", "-", stdin=manifest)
    print(
        f"seeded {name}: {len(jobs)} documents indexed, {len(questions)} questions in {CONFIGMAP}"
    )


if __name__ == "__main__":
    main()
