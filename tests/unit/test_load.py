"""The load tooling's own logic: the summary it reads back, the counts, and what it sends."""

import json
from datetime import UTC, datetime
from typing import Annotated

import pytest
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.testclient import TestClient

from load.run import counts, extract_summary, render_job, tally_pod_log, throughput
from load.seed import multipart

START = datetime(2026, 9, 13, 16, 0, 0, tzinfo=UTC)
END = datetime(2026, 9, 13, 16, 5, 0, tzinfo=UTC)


def access(time: str, route: str | None, status: int) -> str:
    return json.dumps(
        {
            "time": time,
            "logger": "app.access",
            "message": "request",
            "route": route,
            "status": status,
        }
    )


def test_the_summary_is_read_from_between_its_markers() -> None:
    logs = (
        'time="..." level=warning msg="noise"\n'
        '===K6-SUMMARY===\n{"metrics": {}}\n===END-K6-SUMMARY===\n'
    )

    assert extract_summary(logs) == {"metrics": {}}


def test_a_log_without_a_summary_is_an_error() -> None:
    with pytest.raises(ValueError):
        extract_summary("k6 exited before printing one\n")


def test_a_counter_k6_left_out_counts_as_zero() -> None:
    """k6 omits a counter nothing was added to, which is the usual case for failures."""
    summary = {"metrics": {"http_reqs": {"values": {"count": 90, "rate": 3.0}}}}

    assert counts(summary) == {
        "http_reqs": 90,
        "responses_5xx": 0,
        "responses_not_201": 0,
        "requests_without_response": 0,
    }


def test_throughput_counts_only_requests_that_got_a_response() -> None:
    summary = {
        "metrics": {
            "http_reqs": {"values": {"count": 310}},
            "requests_without_response": {"values": {"count": 10}},
        },
        "state": {"testRunDurationMs": 60_000},
    }

    assert throughput(summary) == 5.0


def test_a_pods_log_is_tallied_within_the_run_only() -> None:
    lines = [
        access("2026-09-13T15:59:59+00:00", "/questions", 201),  # before the run
        access("2026-09-13T16:01:00+00:00", "/questions", 201),
        access("2026-09-13T16:02:00+00:00", "/questions", 503),
        access("2026-09-13T16:03:00+00:00", "/health/ready", 200),  # a probe, not load
        json.dumps(
            {
                "time": "2026-09-13T16:02:00+00:00",
                "logger": "app.api.errors",
                "message": "database unavailable",
            }
        ),
        "uvicorn printed this before logging was configured",
        access("2026-09-13T16:05:01+00:00", "/questions", 201),  # after the run
    ]

    tally = tally_pod_log(lines, START, END)

    assert (tally.requests, dict(tally.statuses), tally.database_unavailable) == (
        2,
        {201: 1, 503: 1},
        1,
    )


def test_the_job_template_is_filled_completely() -> None:
    template = "name: ${NAME}\nvus: ${VUS}\nduration: ${DURATION}\nsecret: ${SECRET}\n"

    rendered = render_job(
        template, name="k6-run", vus=30, duration="5m", secret="groundwork-secrets-x"
    )

    assert rendered == "name: k6-run\nvus: 30\nduration: 5m\nsecret: groundwork-secrets-x\n"


def test_a_placeholder_left_unfilled_is_an_error() -> None:
    with pytest.raises(ValueError):
        render_job("${NAME} ${UNKNOWN}", name="k6-run", vus=30, duration="5m", secret="s")


def test_an_upload_body_is_received_as_a_form_with_its_pdf() -> None:
    """The seeder builds its own multipart body, since the standard library offers none."""
    app = FastAPI()

    @app.post("/documents")
    async def receive(
        collection_id: Annotated[str, Form()], file: Annotated[UploadFile, File()]
    ) -> dict[str, object]:
        return {
            "collection_id": collection_id,
            "filename": file.filename,
            "content_type": file.content_type,
            "data": (await file.read()).decode(),
        }

    body, content_type = multipart({"collection_id": "abc"}, "report.pdf", b"%PDF-1.7 body\r\n")
    response = TestClient(app).post(
        "/documents", content=body, headers={"content-type": content_type}
    )

    assert response.json() == {
        "collection_id": "abc",
        "filename": "report.pdf",
        "content_type": "application/pdf",
        "data": "%PDF-1.7 body\r\n",
    }
