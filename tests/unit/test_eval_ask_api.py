from datetime import UTC, datetime

from eval.ask_api import (
    Asked,
    cited_documents,
    collection_name,
    failures,
    judge_answerable,
    judge_unanswerable,
    multipart,
    outcome,
    render,
    tally,
    unfinished,
)
from eval.golden import AnswerableQuestion, Evidence, UnanswerableQuestion


def answerable(id: str, *documents: str) -> AnswerableQuestion:
    return AnswerableQuestion(
        id=id,
        question=f"question {id}",
        evidence=tuple(Evidence(document=d, page=1, quote="q") for d in documents),
    )


def unanswerable(id: str) -> UnanswerableQuestion:
    return UnanswerableQuestion(id=id, question=f"question {id}", reason="near miss")


def answer(*filenames: str, result: str = "answered") -> dict[str, object]:
    return {
        "outcome": result,
        "citations": [{"marker": n, "filename": f} for n, f in enumerate(filenames, start=1)],
    }


def test_the_outcome_word_is_shown_when_the_service_returns_one() -> None:
    assert outcome(201, answer("uam-risk.pdf")) == "answered"
    assert outcome(201, {"outcome": "insufficient_evidence"}) == "insufficient_evidence"


def test_a_refused_request_is_shown_by_its_status_and_detail() -> None:
    """502 and 404 carry no outcome, and the 502's detail says nothing of the cause."""
    declined = {"detail": "no answer could be generated"}

    assert outcome(502, declined) == "502 no answer could be generated"
    assert outcome(404, {"detail": "collection not found"}) == "404 collection not found"
    assert outcome(500, None) == "500"


def test_citations_are_reported_as_the_distinct_documents_in_order() -> None:
    body = answer("uam-risk.pdf", "faint-lateral-cutoff.pdf", "uam-risk.pdf")

    assert cited_documents(body) == ("faint-lateral-cutoff.pdf", "uam-risk.pdf")
    assert cited_documents({"outcome": "insufficient_evidence"}) == ()
    assert cited_documents(None) == ()


def test_an_answerable_question_signals_only_when_it_cites_an_evidence_document() -> None:
    question = answerable("a01", "uam-risk.pdf", "uam-motion-sickness.pdf")

    assert judge_answerable(question, 201, answer("uam-motion-sickness.pdf")).signal
    assert not judge_answerable(question, 201, answer("x59-cumulative-noise-metrics.pdf")).signal
    assert not judge_answerable(question, 201, {"outcome": "insufficient_evidence"}).signal


def test_an_unanswerable_question_signals_only_on_insufficient_evidence() -> None:
    """A 502 is generation failing or declining, not the corpus being silent: crediting it
    as a refusal would score an outage as correct behaviour."""
    question = unanswerable("u01")

    assert judge_unanswerable(question, 201, {"outcome": "insufficient_evidence"}).signal
    assert not judge_unanswerable(question, 201, answer("uam-risk.pdf")).signal
    assert not judge_unanswerable(question, 502, {"detail": "no answer could be generated"}).signal


def test_the_tally_counts_what_was_asked_and_what_signalled() -> None:
    asked = [
        Asked(id="a01", status=201, outcome="answered", cited=("a.pdf",), signal=True),
        Asked(id="a02", status=502, outcome="502 no answer", cited=(), signal=False),
    ]

    assert tally(asked) == (2, 1)
    assert tally([]) == (0, 0)


def test_a_line_names_the_question_its_outcome_and_its_signal() -> None:
    asked = Asked(id="a01", status=201, outcome="answered", cited=("uam-risk.pdf",), signal=True)

    line = render(asked, "cited-evidence-document")

    assert line.startswith("a01  201  answered")
    assert "cited-evidence-document=yes" in line
    assert "uam-risk.pdf" in line


def test_each_run_names_its_collection_after_the_time_it_started() -> None:
    """A repeated name answers 409, and the create would return no id to upload into."""
    started = datetime(2026, 9, 19, 14, 30, 0, tzinfo=UTC)

    assert collection_name(started) == "ask-api 20260919T143000Z"
    assert collection_name(started) != collection_name(started.replace(second=1))


def test_the_upload_body_carries_the_fields_and_the_pdf() -> None:
    body, content_type = multipart({"collection_id": "c1"}, "uam-risk.pdf", b"%PDF-1.7 bytes")

    boundary = content_type.removeprefix("multipart/form-data; boundary=")
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="collection_id"\r\n\r\nc1\r\n' in body
    assert b'name="file"; filename="uam-risk.pdf"' in body
    assert b"Content-Type: application/pdf\r\n\r\n%PDF-1.7 bytes\r\n" in body
    assert body.endswith(f"--{boundary}--\r\n".encode())


def test_waiting_ends_for_a_job_that_failed_as_well_as_one_that_completed() -> None:
    """A failed job never reaches another status: waiting on one would hang to the deadline."""
    jobs = {"j1": "queued", "j2": "running", "j3": "completed", "j4": "failed"}

    assert unfinished(jobs) == ("j1", "j2")
    assert unfinished({"j3": "completed", "j4": "failed"}) == ()


def test_a_failed_ingestion_is_reported_rather_than_asked_over() -> None:
    jobs = {"j1": "completed", "j2": "failed", "j3": "running"}

    assert failures(jobs) == ("j2",)
    assert failures({"j1": "completed"}) == ()


def test_answerable_and_unanswerable_lines_keep_the_same_columns() -> None:
    """The signals differ in length, and unpadded they moved the cited column between
    the two groups, which the readme's sample output showed aligned."""
    answered = Asked(id="a01", status=201, outcome="answered", cited=("a.pdf",), signal=True)
    refused = Asked(id="u01", status=201, outcome="insufficient_evidence", cited=(), signal=True)

    lines = [render(answered, "cited-evidence-document"), render(refused, "refused")]

    assert lines[0].index("a.pdf") == lines[1].index("-")
