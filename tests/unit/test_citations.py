from app.generation.citations import check_citations
from app.generation.context import Passage
from app.generation.generator import Generation, Statement

FIVE_PASSAGES = [Passage(n, f"passage {n}", "report.pdf", n, n) for n in range(1, 6)]


def answer(*statements: tuple[str, tuple[int, ...]]) -> Generation:
    return Generation(
        statements=tuple(Statement(text, citations) for text, citations in statements),
        insufficient_evidence=False,
    )


def test_valid_citations_are_kept_and_rendered_as_markers() -> None:
    generation = answer(
        ("Workers refresh a heartbeat.", (1,)), ("Stale jobs are reclaimed.", (2, 3))
    )

    checked = check_citations(generation, FIVE_PASSAGES)

    assert checked.text == "Workers refresh a heartbeat [1]. Stale jobs are reclaimed [2][3]."
    assert (checked.cited, checked.invalid_citations) == ((1, 2, 3), 0)


def test_citations_of_passages_never_supplied_are_dropped_and_counted_once_each() -> None:
    checked = check_citations(answer(("A claim.", (1, 9, 0, 9, -2))), FIVE_PASSAGES)

    assert checked.statements == (Statement("A claim.", (1,)),)
    assert checked.invalid_citations == 3


def test_a_statement_left_without_a_valid_citation_is_dropped() -> None:
    generation = answer(("Supported.", (2,)), ("Invented.", (7,)), ("Uncited.", ()))

    checked = check_citations(generation, FIVE_PASSAGES)

    assert checked.text == "Supported [2]."
    assert checked.invalid_citations == 1


def test_nothing_survives_when_every_citation_is_invalid() -> None:
    checked = check_citations(answer(("Invented.", (6,)), ("Also invented.", (8,))), FIVE_PASSAGES)

    assert (checked.statements, checked.cited, checked.invalid_citations) == ((), (), 2)
    assert checked.text == ""


def test_each_passage_is_cited_once_in_first_cited_order() -> None:
    checked = check_citations(answer(("First.", (3, 1, 3)), ("Second.", (1, 2))), FIVE_PASSAGES)

    assert checked.statements[0].citations == (3, 1)
    assert checked.cited == (3, 1, 2)


def test_markers_follow_a_statement_without_closing_punctuation() -> None:
    checked = check_citations(answer(("Heartbeats every batch", (1,))), FIVE_PASSAGES)

    assert checked.text == "Heartbeats every batch [1]"


def test_validity_depends_on_the_passages_supplied_not_an_assumed_five() -> None:
    """With two passages supplied, [3] is as invalid as [99]."""
    checked = check_citations(answer(("A claim.", (2, 3))), FIVE_PASSAGES[:2])

    assert (checked.cited, checked.invalid_citations) == ((2,), 1)
