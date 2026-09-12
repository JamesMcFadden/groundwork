from collections.abc import Sequence

from eval.prefix import MIN_NET_FIXED, QUERY_INSTRUCTION, PrefixComparison, query_embedders
from eval.retrieval import AnswerableResult, RetrievalReport


def report(*ranks: int | None) -> RetrievalReport:
    """Questions q0, q1, ... whose first hits rank as given within ten results."""
    return RetrievalReport(
        retriever="dense",
        answerable=tuple(
            AnswerableResult(
                question_id=f"q{n}",
                rank_at_5=rank if rank is not None and rank <= 5 else None,
                rank_at_10=rank,
                best_score=0.5,
            )
            for n, rank in enumerate(ranks)
        ),
        unanswerable=(),
    )


def test_the_rule_is_the_one_pre_registered() -> None:
    """Fixed in the roadmap before any result existed; changing it needs a dated note."""
    assert MIN_NET_FIXED == 2
    assert QUERY_INSTRUCTION == "Represent this sentence for searching relevant passages: "


def test_flips_are_the_questions_fixed_and_broken_at_5() -> None:
    comparison = PrefixComparison(
        without=report(1, None, 3, None), with_prefix=report(1, 2, None, 4)
    )

    assert comparison.fixed == ["q1", "q3"]
    assert comparison.broken == ["q2"]
    assert comparison.net_fixed == 1


def test_the_prefix_is_adopted_when_it_fixes_two_more_than_it_breaks_and_mrr_holds() -> None:
    comparison = PrefixComparison(without=report(None, None, 1), with_prefix=report(2, 3, 1))

    assert comparison.net_fixed == 2
    assert comparison.adopt


def test_one_net_fix_is_not_enough_to_adopt_the_prefix() -> None:
    """At n=30 one question is noise; the rule asks for two."""
    comparison = PrefixComparison(without=report(None, 1, 1), with_prefix=report(1, 1, 1))

    assert comparison.net_fixed == 1
    assert not comparison.adopt


def test_a_fall_in_mrr_blocks_adoption_even_with_enough_fixes() -> None:
    """Hits moved from the top of the list to its bottom are not an improvement."""
    comparison = PrefixComparison(without=report(1, 1, None, None), with_prefix=report(5, 5, 5, 5))

    assert comparison.net_fixed == 2
    assert comparison.with_prefix.mrr_at_10 < comparison.without.mrr_at_10
    assert not comparison.adopt


def test_the_instruction_is_added_once_and_only_to_the_prefixed_variant() -> None:
    embedded: list[str] = []

    def embed_passages(texts: Sequence[str]) -> list[list[float]]:
        embedded.extend(texts)
        return [[0.0] for _ in texts]

    without, with_prefix = query_embedders(embed_passages)
    without("How many studies met the criteria?")
    with_prefix("How many studies met the criteria?")

    assert embedded == [
        "How many studies met the criteria?",
        QUERY_INSTRUCTION + "How many studies met the criteria?",
    ]
