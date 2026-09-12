from app.retrieval.search import Retriever
from eval.results import render_summary
from eval.retrieval import AnswerableResult, RetrievalReport, UnanswerableResult

METADATA = {
    "git": {"commit": "0" * 40, "uncommitted_changes": False},
    "corpus_manifest_sha256": "c" * 64,
    "golden_set_sha256": "g" * 64,
}
CHUNK_COUNTS = {"report.pdf": 12}


def report(retriever: Retriever, best_score: float | None) -> RetrievalReport:
    return RetrievalReport(
        retriever=retriever,
        answerable=(
            AnswerableResult("a01", rank_at_5=1, rank_at_10=1, best_score=best_score),
            AnswerableResult("a02", rank_at_5=None, rank_at_10=7, best_score=best_score),
        ),
        unanswerable=(UnanswerableResult("u01", best_score=best_score),),
    )


def test_the_summary_is_headed_with_the_retriever_that_ran() -> None:
    cases: tuple[tuple[Retriever, float | None], ...] = (("dense", 0.8), ("hybrid", None))
    for retriever, best_score in cases:
        summary = render_summary(report(retriever, best_score), METADATA, CHUNK_COUNTS)

        assert summary.splitlines()[0] == f"## Retrieval evaluation ({retriever})"


def test_best_chunk_scores_are_summarised_only_for_dense_search() -> None:
    """Fused scores are built from ranks and say nothing about a relevance threshold."""
    dense = render_summary(report("dense", 0.8), METADATA, CHUNK_COUNTS)
    hybrid = render_summary(report("hybrid", None), METADATA, CHUNK_COUNTS)

    assert "Best chunk score: answerable min 0.800" in dense
    assert "Best chunk score" not in hybrid
