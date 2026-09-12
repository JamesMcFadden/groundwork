from typing import Any

import pytest

from eval.answering import STAGES, AnsweringReport, AnswerResult, markers_in


def result(
    question_id: str = "a01",
    answerable: bool = True,
    outcome: str = "answered",
    markers: tuple[int, ...] = (1,),
    cited: tuple[int, ...] = (1,),
    invalid: int | None = 0,
    **timings: Any,
) -> AnswerResult:
    return AnswerResult(
        question_id=question_id,
        answerable=answerable,
        retriever="dense",
        outcome=outcome,
        answer_text=None,
        markers=markers,
        cited_ranks=cited,
        invalid_citations=invalid,
        error_class=None,
        input_tokens=None,
        output_tokens=None,
        timings={stage: timings.get(stage) for stage in STAGES},
    )


def test_markers_are_read_once_each_from_the_answer_text() -> None:
    assert markers_in("Workers claim jobs [1]. Stale jobs are reclaimed [3][1].") == (1, 3)
    assert markers_in(None) == ()


def test_only_insufficient_evidence_counts_as_refusing_an_unanswerable_question() -> None:
    """A safety refusal or a failure is not the service recognising what it cannot answer."""
    report = AnsweringReport(
        tuple(
            result(f"u0{n}", answerable=False, outcome=outcome, invalid=None)
            for n, outcome in enumerate(["insufficient_evidence", "declined", "failed", "answered"])
        )
    )

    assert report.refused == 1
    assert report.false_refusals == 0


def test_false_refusals_are_answerable_questions_recorded_as_insufficient_evidence() -> None:
    report = AnsweringReport(
        (
            result("a01", outcome="insufficient_evidence", markers=(), cited=()),
            result("a02"),
            result("a03", outcome="declined", markers=(), cited=(), invalid=None),
            result("u01", answerable=False, outcome="insufficient_evidence", markers=(), cited=()),
        )
    )

    assert report.false_refusals == 1
    assert report.count("declined") == 1


def test_a_marker_naming_no_cited_chunk_is_unresolved() -> None:
    unresolved = result(markers=(1, 4), cited=(1,))
    report = AnsweringReport((unresolved, result("a02")))

    assert unresolved.unresolved_markers == (4,)
    assert (report.unresolved_markers, report.markers_checked) == (1, 3)


def test_the_raw_invalid_citation_rate_is_rejected_over_all_checked_citations() -> None:
    """Only questions whose answers reached validation are counted; a declined one did not."""
    report = AnsweringReport(
        (
            result("a01", markers=(1, 3), cited=(1, 3), invalid=2),
            result("a02", invalid=0),
            result("a03", outcome="declined", markers=(), cited=(), invalid=None),
        )
    )

    assert (report.rejected_citations, report.accepted_citations) == (2, 3)
    assert report.questions_checked == 2
    assert report.raw_invalid_citation_rate == pytest.approx(0.4)


def test_there_is_no_rate_when_no_citation_was_checked() -> None:
    report = AnsweringReport((result(outcome="failed", markers=(), cited=(), invalid=None),))

    assert report.raw_invalid_citation_rate is None


def test_latency_covers_only_the_questions_that_reached_a_stage() -> None:
    report = AnsweringReport(
        (
            result("a01", embed_ms=4, llm_ms=900),
            result("a02", embed_ms=6, llm_ms=1500),
            result("a03", outcome="insufficient_evidence", embed_ms=5),
        )
    )

    assert report.latency("embed_ms") == {"p50": 5, "p95": 6, "n": 3}
    assert report.latency("llm_ms") == {"p50": 900, "p95": 1500, "n": 2}
    assert report.latency("prep_ms") == {"p50": None, "p95": None, "n": 0}
