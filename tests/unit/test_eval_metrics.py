import uuid

import pytest

from app.retrieval.dense import RetrievedChunk
from eval.golden import Evidence
from eval.metrics import first_hit_rank, is_hit, mean_reciprocal_rank, percentile, recall_at

QUOTE = "Blocking out time for relaxation"
EVIDENCE = (Evidence("fatigue.pdf", 29, QUOTE),)


def chunk(
    text: str = f"Four strategies were tested. {QUOTE} was among the most effective.",
    filename: str = "fatigue.pdf",
    pages: tuple[int, int] = (28, 29),
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=1,
        document_id=uuid.UUID(int=1),
        filename=filename,
        text=text,
        page_start=pages[0],
        page_end=pages[1],
        score=0.5,
    )


def test_a_chunk_holding_the_quote_on_the_evidence_page_is_a_hit() -> None:
    assert is_hit(chunk(), EVIDENCE)


def test_the_quote_matches_ignoring_case_and_line_breaks() -> None:
    assert is_hit(chunk(text="strategies: blocking out\ntime FOR  relaxation, and more"), EVIDENCE)


def test_a_chunk_from_another_document_is_not_a_hit() -> None:
    assert not is_hit(chunk(filename="motion-sickness.pdf"), EVIDENCE)


def test_a_chunk_whose_pages_miss_the_evidence_page_is_not_a_hit() -> None:
    """The same words elsewhere in the document are not the evidence a citation points to."""
    assert not is_hit(chunk(pages=(30, 31)), EVIDENCE)


def test_a_chunk_on_the_evidence_page_without_the_quote_is_not_a_hit() -> None:
    """Page overlap alone would credit a neighbouring chunk that does not hold the answer."""
    assert not is_hit(chunk(text="Scaling back obligations was less effective."), EVIDENCE)


def test_any_one_evidence_location_counts() -> None:
    evidence = (Evidence("other.pdf", 3, "an unrelated quote"), *EVIDENCE)

    assert is_hit(chunk(), evidence)


def test_the_rank_is_that_of_the_first_hit() -> None:
    miss = chunk(text="nothing relevant")

    assert first_hit_rank([miss, chunk(), chunk()], EVIDENCE) == 2
    assert first_hit_rank([miss, miss], EVIDENCE) is None
    assert first_hit_rank([], EVIDENCE) is None


def test_recall_is_the_share_of_questions_with_a_hit_within_k() -> None:
    assert recall_at([1, None, 5, 7], k=5) == 0.5


def test_mrr_averages_reciprocal_ranks_within_k_counting_the_rest_as_zero() -> None:
    assert mean_reciprocal_rank([1, 2, None, 4], k=10) == pytest.approx((1 + 0.5 + 0.25) / 4)
    assert mean_reciprocal_rank([1, 2, None, 4], k=3) == pytest.approx((1 + 0.5) / 4)


def test_a_percentile_is_the_nearest_rank_so_every_value_was_observed() -> None:
    assert percentile([40, 10, 30, 20], 50) == 20
    assert percentile([40, 10, 30, 20], 95) == 40
    assert percentile([7], 95) == 7
    assert percentile([], 50) is None


def test_scoring_no_questions_is_an_error() -> None:
    with pytest.raises(ValueError, match="no questions"):
        recall_at([], k=5)
