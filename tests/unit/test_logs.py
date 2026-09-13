import io
import json
import logging
import re
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.logs import JsonFormatter, configure_logging
from app.main import create_app
from app.middleware import RequestLogMiddleware

GENERATED_ID = re.compile(r"[0-9a-f]{32}")
ACCESS_FIELDS = {
    "time",
    "level",
    "logger",
    "message",
    "request_id",
    "method",
    "route",
    "status",
    "duration_ms",
}

Lines = Callable[[], list[dict[str, Any]]]


@pytest.fixture
def output() -> Iterator[io.StringIO]:
    """Everything the JSON formatter writes while the test runs."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    yield stream
    root.removeHandler(handler)
    root.setLevel(level)


@pytest.fixture
def lines(output: io.StringIO) -> Lines:
    return lambda: [json.loads(line) for line in output.getvalue().splitlines()]


def access(lines: Lines) -> list[dict[str, Any]]:
    return [line for line in lines() if line["logger"] == "app.access"]


def served(lines: Lines) -> list[dict[str, Any]]:
    """Lines the app wrote while serving. httpx logs each call from the client's side."""
    return [line for line in lines() if line["logger"] in ("tests.route", "app.access")]


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.add_middleware(RequestLogMiddleware)

    @app.get("/items/{item_id}")
    def get_item(item_id: str) -> dict[str, str]:
        logging.getLogger("tests.route").info("inside the route")
        if item_id == "boom":
            raise RuntimeError("boom")
        return {"id": item_id}

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_a_record_is_one_json_line_with_its_extra_fields(lines: Lines) -> None:
    job_id = uuid.uuid4()

    logging.getLogger("tests.logs").info("claimed %s", "a job", extra={"job_id": job_id})

    [line] = lines()
    assert (line["level"], line["logger"], line["message"]) == (
        "INFO",
        "tests.logs",
        "claimed a job",
    )
    assert line["job_id"] == str(job_id)
    assert line["time"].endswith("+00:00")
    assert "request_id" not in line


def test_uvicorns_coloured_copy_of_a_message_is_left_out(lines: Lines) -> None:
    """uvicorn repeats some messages with terminal colour codes, which are noise in JSON."""
    logging.getLogger("uvicorn.error").info(
        "Started server process [%d]",
        42,
        extra={"color_message": "Started server process [\x1b[36m%d\x1b[0m]"},
    )

    [line] = lines()
    assert line["message"] == "Started server process [42]"
    assert "color_message" not in line


def test_an_exception_is_written_with_its_traceback(lines: Lines) -> None:
    try:
        raise RuntimeError("storage unreachable")
    except RuntimeError:
        logging.getLogger("tests.logs").exception("job failed")

    [line] = lines()
    assert "RuntimeError: storage unreachable" in line["exception"]


def test_a_request_without_an_id_is_given_one_that_its_lines_carry(
    client: TestClient, lines: Lines
) -> None:
    response = client.get("/items/abc")

    returned = response.headers["X-Request-ID"]
    assert GENERATED_ID.fullmatch(returned)
    assert [line["request_id"] for line in served(lines)] == [returned, returned]


def test_a_callers_request_id_is_kept(client: TestClient, lines: Lines) -> None:
    response = client.get("/items/abc", headers={"X-Request-ID": "trace-01.a_b"})

    assert response.headers["X-Request-ID"] == "trace-01.a_b"
    assert [line["request_id"] for line in served(lines)] == ["trace-01.a_b", "trace-01.a_b"]


@pytest.mark.parametrize("unacceptable", ["x" * 129, "has space", "semi;colon", ""])
def test_an_unacceptable_request_id_is_replaced(
    client: TestClient, lines: Lines, unacceptable: str
) -> None:
    """Only short, plain ids are kept, so a caller cannot put arbitrary text in the logs."""
    response = client.get("/items/abc", headers={"X-Request-ID": unacceptable})

    returned = response.headers["X-Request-ID"]
    assert GENERATED_ID.fullmatch(returned)
    assert [line["request_id"] for line in served(lines)] == [returned, returned]


def test_each_request_is_logged_once_by_route_template_without_headers(
    client: TestClient, output: io.StringIO, lines: Lines
) -> None:
    """The line names the template, not the path, and never carries what the caller sent."""
    client.get("/items/abc", headers={"X-API-Key": "secret-key-value"})

    [line] = access(lines)
    assert set(line) == ACCESS_FIELDS
    assert (line["level"], line["method"], line["route"], line["status"]) == (
        "INFO",
        "GET",
        "/items/{item_id}",
        200,
    )
    assert isinstance(line["duration_ms"], int)
    assert "secret-key-value" not in output.getvalue()


def test_a_request_that_raises_is_logged_as_a_500_with_its_traceback(
    client: TestClient, lines: Lines
) -> None:
    response = client.get("/items/boom")

    assert response.status_code == 500
    [line] = access(lines)
    assert (line["level"], line["route"], line["status"]) == ("ERROR", "/items/{item_id}", 500)
    assert "RuntimeError: boom" in line["exception"]
    assert GENERATED_ID.fullmatch(line["request_id"])


def test_a_path_matching_no_route_is_logged_without_a_template(
    client: TestClient, lines: Lines
) -> None:
    client.get("/nowhere")

    [line] = access(lines)
    assert (line["route"], line["status"]) == (None, 404)


def test_the_app_returns_a_request_id_with_its_responses() -> None:
    response = TestClient(create_app()).get("/health/live")

    assert GENERATED_ID.fullmatch(response.headers["X-Request-ID"])


def test_configure_logging_writes_json_lines_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        configure_logging()
        logging.getLogger("tests.logs").info("worker started")
    finally:
        root.handlers[:], root.level = handlers, level

    line = json.loads(capsys.readouterr().out)
    assert (line["level"], line["message"]) == ("INFO", "worker started")
