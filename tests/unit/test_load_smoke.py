from eval.golden import AnswerableQuestion, Evidence, GoldenSet
from load.smoke import choose_question, cites, ingestion_seconds, verdict


def question(id: str, *documents: str) -> AnswerableQuestion:
    return AnswerableQuestion(
        id=id,
        question=f"question {id}",
        evidence=tuple(Evidence(document=d, page=1, quote="q") for d in documents),
    )


def test_the_first_question_in_file_order_with_evidence_in_the_document_is_chosen() -> None:
    """The rule fixed which question before any run: choosing another would move the goalpost."""
    golden = GoldenSet(
        answerable=(
            question("a01", "other.pdf"),
            question("a02", "other.pdf", "uam-risk.pdf"),
            question("a03", "uam-risk.pdf"),
        ),
        unanswerable=(),
        sha256="0" * 64,
    )

    assert choose_question(golden, "uam-risk.pdf").id == "a02"


def test_an_answer_counts_only_if_a_citation_names_the_document() -> None:
    assert cites(
        {"citations": [{"filename": "other.pdf"}, {"filename": "uam-risk.pdf"}]}, "uam-risk.pdf"
    )
    assert not cites({"citations": [{"filename": "other.pdf"}]}, "uam-risk.pdf")
    assert not cites({"outcome": "insufficient_evidence"}, "uam-risk.pdf")


def test_the_criterion_is_met_only_when_every_check_passes() -> None:
    assert verdict({"a": {"passed": True}, "b": {"passed": True}}) == "met"
    assert verdict({"a": {"passed": True}, "b": {"passed": False}}) == "not met"
    assert verdict({}) == "not met"


def test_ingestion_time_comes_from_the_job_timestamps() -> None:
    job = {
        "created_at": "2026-09-14T00:40:00+00:00",
        "started_at": "2026-09-14T00:40:01.500000+00:00",
        "finished_at": "2026-09-14T00:40:22+00:00",
    }

    assert ingestion_seconds(job) == {"upload_to_indexed": 22.0, "worker": 20.5}
    assert ingestion_seconds({"created_at": job["created_at"]}) == {
        "upload_to_indexed": None,
        "worker": None,
    }
