"""Ask the golden set through a running API, to watch the service answer it.

Not a measurement. The API returns the passages an answer cited, not the ranked chunks
behind them, so nothing here computes Recall@5 or MRR: a citation naming the right
document is far coarser than evidence retrieved in the right chunk, and a question can
cite the right report for the wrong reason. `python -m eval` scores the golden set, from
the chunks, and its figures are the ones to cite. This walks the same questions through
the deployed path instead — the API key, the worker's chunks, search, and generation —
which the harness skips by writing chunks itself.

    docker compose up -d --build --wait
    KEY=... uv run python -m eval.ask_api --upload

`--upload` does the whole walk: a new collection, the frozen corpus uploaded through
`POST /documents`, a wait for every job, then the questions. `--collection <id>` asks
against a collection that already holds the corpus instead. Questions are read through
the harness's own loader, so they are the frozen ones, checked against the frozen corpus,
and never retyped here.

Generation is charged per question unless GENERATOR is the stub, so --limit asks only the
first few of each kind.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from eval.corpus import CORPUS_DIR, CorpusDocument, CorpusError, load_corpus
from eval.golden import AnswerableQuestion, GoldenSetError, UnanswerableQuestion, load_golden

DEFAULT_BASE = "http://localhost:8000"
# Generation dominates a question, and a corpus PDF is megabytes: the API's own stages are
# milliseconds either way.
TIMEOUT_SECONDS = 120
# Ingesting the six reports takes seconds each locally, minutes on a cold embedding cache.
JOB_TIMEOUT_SECONDS = 600
POLL_SECONDS = 2
FINISHED = ("completed", "failed")


@dataclass(frozen=True, slots=True)
class Asked:
    """One question and what the service did with it.

    `signal` is what that kind of question was asking of the service: for an answerable
    one, that a citation named a document its evidence names; for an unanswerable one,
    that the service refused. Neither is a score, and both are printed beside the outcome
    rather than totalled into a figure.
    """

    id: str
    status: int
    outcome: str
    cited: tuple[str, ...]
    signal: bool


def outcome(status: int, body: Any) -> str:
    """What the service did: its own outcome word, or the status and detail of a refusal.

    A 502 says nothing of the cause by design, so its detail is all there is to show.
    """
    if isinstance(body, Mapping):
        if "outcome" in body:
            return str(body["outcome"])
        if "detail" in body:
            return f"{status} {body['detail']}"
    return str(status)


def cited_documents(body: Any) -> tuple[str, ...]:
    """The distinct documents an answer cited, in sorted order; empty when it cited none."""
    if not isinstance(body, Mapping):
        return ()
    citations = body.get("citations") or []
    return tuple(sorted({str(citation["filename"]) for citation in citations}))


def judge_answerable(question: AnswerableQuestion, status: int, body: Any) -> Asked:
    """An answerable question signals when a citation names a document its evidence names."""
    expected = {evidence.document for evidence in question.evidence}
    cited = cited_documents(body)
    return Asked(
        id=question.id,
        status=status,
        outcome=outcome(status, body),
        cited=cited,
        signal=bool(expected & set(cited)),
    )


def judge_unanswerable(question: UnanswerableQuestion, status: int, body: Any) -> Asked:
    """An unanswerable question signals only on insufficient evidence.

    A 502 is generation failing or declining, not the service finding the corpus silent,
    and counting it as a refusal would credit an outage as correct behaviour.
    """
    result = outcome(status, body)
    return Asked(
        id=question.id,
        status=status,
        outcome=result,
        cited=cited_documents(body),
        signal=status == 201 and result == "insufficient_evidence",
    )


def render(asked: Asked, signal: str) -> str:
    """One line per question: what came back, and whether it carried the signal."""
    cited = ", ".join(asked.cited) if asked.cited else "-"
    carried = "yes" if asked.signal else "no"
    return f"{asked.id}  {asked.status}  {asked.outcome:<22}  {signal}={carried}  {cited}"


def tally(asked: Sequence[Asked]) -> tuple[int, int]:
    """How many were asked, and how many carried their signal."""
    return len(asked), sum(1 for one in asked if one.signal)


def collection_name(started: datetime) -> str:
    """A name unique to the run: `POST /collections` answers 409 to one already used."""
    return f"ask-api {started.strftime('%Y%m%dT%H%M%SZ')}"


def multipart(fields: Mapping[str, str], filename: str, data: bytes) -> tuple[bytes, str]:
    """A multipart/form-data body holding text fields and one PDF, and its content type.

    Built here rather than imported from `load.seed`, which builds the same body for the
    cluster: the evaluation harness has no business importing the load-testing package,
    which is tied to a kind context.
    """
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


def unfinished(jobs: Mapping[str, str]) -> tuple[str, ...]:
    """The ids still queued or running, in sorted order.

    A failed job is finished. Waiting on one for a status it will never reach would hang
    until the deadline instead of reporting the failure.
    """
    return tuple(sorted(job for job, status in jobs.items() if status not in FINISHED))


def failures(jobs: Mapping[str, str]) -> tuple[str, ...]:
    """The ids that finished as failures, in sorted order."""
    return tuple(sorted(job for job, status in jobs.items() if status == "failed"))


def call(
    base: str,
    key: str,
    method: str,
    path: str,
    body: bytes | None = None,
    content_type: str = "application/json",
) -> tuple[int, Any]:
    """A request to the API. An HTTP error is a status to report, not an exception."""
    request = urllib.request.Request(
        f"{base}{path}",
        data=body,
        headers={"content-type": content_type, "x-api-key": key},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def ask(base: str, key: str, collection: str, question: str) -> tuple[int, Any]:
    """Put one question to the API."""
    body = json.dumps({"collection_id": collection, "question": question}).encode()
    return call(base, key, "POST", "/questions", body)


def create_collection(base: str, key: str, name: str) -> str:
    """Create the run's collection and return its id."""
    status, body = call(base, key, "POST", "/collections", json.dumps({"name": name}).encode())
    if status != 201 or not isinstance(body, Mapping):
        raise SystemExit(f"ask_api: creating the collection answered {status}: {body}")
    return str(body["id"])


def upload(base: str, key: str, collection: str, document: CorpusDocument) -> str:
    """Upload one corpus document and return the id of the job queued for it."""
    body, content_type = multipart(
        {"collection_id": collection}, document.filename, document.path.read_bytes()
    )
    status, answer = call(base, key, "POST", "/documents", body, content_type)
    if status != 202 or not isinstance(answer, Mapping):
        raise SystemExit(f"ask_api: uploading {document.filename} answered {status}: {answer}")
    return str(answer["job_id"])


def wait_for_jobs(base: str, key: str, jobs: Mapping[str, str]) -> dict[str, str]:
    """Poll every job until it finishes, returning each one's final status.

    Questions asked over a half-indexed collection answer from whatever chunks exist,
    which reads as a retrieval failure rather than a race.
    """
    statuses = dict(jobs)
    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    while pending := unfinished(statuses):
        if time.monotonic() > deadline:
            raise SystemExit(
                f"ask_api: {len(pending)} job(s) unfinished after {JOB_TIMEOUT_SECONDS} s"
            )
        time.sleep(POLL_SECONDS)
        for job in pending:
            status, body = call(base, key, "GET", f"/jobs/{job}")
            if status == 200 and isinstance(body, Mapping):
                statuses[job] = str(body["status"])
    return statuses


def ingest_corpus(base: str, key: str, documents: Sequence[CorpusDocument]) -> str:
    """Create a collection, upload the corpus into it, wait for indexing, return its id."""
    collection = create_collection(base, key, collection_name(datetime.now(UTC)))
    print(f"collection {collection}")
    jobs = {upload(base, key, collection, document): document.filename for document in documents}
    print(f"uploaded {len(jobs)} documents, waiting for indexing")
    finished = wait_for_jobs(base, key, dict.fromkeys(jobs, "queued"))
    broken = failures(finished)
    if broken:
        named = ", ".join(sorted(jobs[job] for job in broken))
        raise SystemExit(f"ask_api: ingestion failed for {named}; see the worker's logs")
    return collection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.ask_api",
        description="Ask the golden set through a running API; python -m eval scores it.",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--collection", help="a collection already holding the corpus")
    source.add_argument(
        "--upload", action="store_true", help="upload the corpus to a new collection first"
    )
    parser.add_argument(
        "--base", default=DEFAULT_BASE, help=f"API base URL (default: {DEFAULT_BASE})"
    )
    parser.add_argument("--limit", type=int, help="ask only the first N of each kind")
    parser.add_argument("--dry-run", action="store_true", help="list the questions, ask nothing")
    args = parser.parse_args(argv)
    if not args.dry_run and not (args.collection or args.upload):
        parser.error("one of --collection, --upload, or --dry-run is required")

    try:
        corpus = load_corpus()
        golden = load_golden([document.filename for document in corpus.documents])
    except (CorpusError, GoldenSetError) as error:
        print(f"ask_api: {error}", file=sys.stderr)
        return 2

    answerable = golden.answerable[: args.limit]
    unanswerable = golden.unanswerable[: args.limit]

    if args.dry_run:
        listed: list[AnswerableQuestion | UnanswerableQuestion] = [*answerable, *unanswerable]
        for question in listed:
            print(f"{question.id}  {question.question}")
        return 0

    key = os.environ.get("KEY")
    if not key:
        print(
            "ask_api: set KEY to API_KEY from .env, the key the API was started with",
            file=sys.stderr,
        )
        return 2

    try:
        collection = (
            ingest_corpus(args.base, key, corpus.documents) if args.upload else args.collection
        )
        answered = [
            judge_answerable(question, *ask(args.base, key, collection, question.question))
            for question in answerable
        ]
        refused = [
            judge_unanswerable(question, *ask(args.base, key, collection, question.question))
            for question in unanswerable
        ]
    except urllib.error.URLError as error:
        print(f"ask_api: {args.base} unreachable: {error.reason}", file=sys.stderr)
        return 2

    for asked in answered:
        print(render(asked, "cited-evidence-document"))
    for asked in refused:
        print(render(asked, "refused"))

    asked_count, signalled = tally(answered)
    print(f"\nanswerable:   {signalled}/{asked_count} cited a document their evidence names")
    asked_count, signalled = tally(refused)
    print(f"unanswerable: {signalled}/{asked_count} refused")
    print(f"\n{CORPUS_DIR.name}: a walkthrough, not a measurement. `python -m eval` scores it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
