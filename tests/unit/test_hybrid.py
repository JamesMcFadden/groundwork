import uuid
from dataclasses import replace

import pytest

from app.retrieval.dense import RetrievedChunk
from app.retrieval.hybrid import RRF_K, reciprocal_rank_fusion


def ranking(*chunk_ids: int) -> list[RetrievedChunk]:
    """Chunks in rank order, first id first. Their own scores play no part in fusion."""
    return [
        RetrievedChunk(
            chunk_id=chunk_id,
            document_id=uuid.UUID(int=chunk_id),
            filename=f"report-{chunk_id}.pdf",
            text=f"passage {chunk_id}",
            page_start=chunk_id,
            page_end=chunk_id,
            score=0.9,
        )
        for chunk_id in chunk_ids
    ]


def ids(results: list[RetrievedChunk]) -> list[int]:
    return [result.chunk_id for result in results]


def test_each_chunk_scores_the_sum_of_its_reciprocal_ranks() -> None:
    fused = reciprocal_rank_fusion([ranking(1, 2, 3), ranking(3, 4)], limit=10)

    assert ids(fused) == [3, 1, 2, 4]
    assert [result.score for result in fused] == pytest.approx(
        [1 / (RRF_K + 3) + 1 / (RRF_K + 1), 1 / (RRF_K + 1), 1 / (RRF_K + 2), 1 / (RRF_K + 2)]
    )


def test_a_chunk_ranked_well_by_both_beats_one_ranked_first_by_only_one() -> None:
    fused = reciprocal_rank_fusion([ranking(10, 20), ranking(30, 20)], limit=10)

    assert ids(fused) == [20, 10, 30]


def test_equal_scores_are_ordered_by_chunk_id() -> None:
    """The same rankings fuse the same way, whichever list a tied chunk came from."""
    assert ids(reciprocal_rank_fusion([ranking(9), ranking(4)], limit=10)) == [4, 9]
    assert ids(reciprocal_rank_fusion([ranking(4), ranking(9)], limit=10)) == [4, 9]


def test_with_one_ranking_empty_the_other_keeps_its_order() -> None:
    """A question of stopwords alone finds nothing by full text; dense search's order stands."""
    assert ids(reciprocal_rank_fusion([ranking(3, 1, 2), []], limit=10)) == [3, 1, 2]


def test_at_most_limit_chunks_are_returned_best_first() -> None:
    fused = reciprocal_rank_fusion([ranking(7, 6, 5, 4, 3, 2, 1), []], limit=5)

    assert ids(fused) == [7, 6, 5, 4, 3]


def test_each_result_is_its_chunk_carrying_the_fused_score() -> None:
    (chunk,) = ranking(5)

    (fused,) = reciprocal_rank_fusion([[chunk], ranking(5)], limit=10)

    assert replace(fused, score=0.0) == replace(chunk, score=0.0)
    assert fused.score == pytest.approx(2 / (RRF_K + 1))
