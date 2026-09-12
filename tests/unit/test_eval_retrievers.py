from app.retrieval.search import Retriever
from eval.retrieval import AnswerableResult, RetrievalReport
from eval.retrievers import MIN_NET_FIXED, RetrieverComparison


def report(retriever: Retriever, *ranks: int | None) -> RetrievalReport:
    """Questions q0, q1, ... whose first hits rank as given within ten results."""
    return RetrievalReport(
        retriever=retriever,
        answerable=tuple(
            AnswerableResult(
                question_id=f"q{n}",
                rank_at_5=rank if rank is not None and rank <= 5 else None,
                rank_at_10=rank,
                best_score=None,
            )
            for n, rank in enumerate(ranks)
        ),
        unanswerable=(),
    )


def compare(dense: tuple[int | None, ...], hybrid: tuple[int | None, ...]) -> RetrieverComparison:
    return RetrieverComparison(dense=report("dense", *dense), hybrid=report("hybrid", *hybrid))


def test_the_rule_is_the_one_pre_registered() -> None:
    """Fixed in the roadmap before any hybrid result existed; changing it needs a dated note."""
    assert MIN_NET_FIXED == 2


def test_flips_are_the_questions_hybrid_fixed_and_broke_at_5() -> None:
    comparison = compare(dense=(1, None, 3, None, 7), hybrid=(1, 2, None, None, 4))

    assert comparison.fixed == ["q1", "q4"]
    assert comparison.broken == ["q2"]
    assert comparison.net_fixed == 1


def test_hybrid_is_adopted_when_it_fixes_two_more_than_it_breaks_and_mrr_holds() -> None:
    comparison = compare(dense=(None, None, 1), hybrid=(2, 3, 1))

    assert comparison.net_fixed == 2
    assert comparison.adopt


def test_one_net_fix_is_not_enough_to_adopt_hybrid() -> None:
    """At n=30 one question is noise; the rule asks for two, as it did of the prefix."""
    comparison = compare(dense=(None, 1, 1), hybrid=(1, 1, 1))

    assert comparison.net_fixed == 1
    assert comparison.hybrid.mrr_at_10 > comparison.dense.mrr_at_10
    assert not comparison.adopt


def test_a_fall_in_mrr_blocks_adoption_even_with_enough_fixes() -> None:
    """Hits moved from the top of the list to its bottom are not an improvement."""
    comparison = compare(dense=(1, 1, None, None), hybrid=(5, 5, 5, 5))

    assert comparison.net_fixed == 2
    assert comparison.hybrid.mrr_at_10 < comparison.dense.mrr_at_10
    assert not comparison.adopt
