import hashlib
import json
import re
from typing import Any

import pytest

from app.ingest.chunk import OVERLAP_TOKENS
from eval.golden import (
    MAX_QUOTE_TOKENS,
    AnswerableQuestion,
    Evidence,
    GoldenSet,
    GoldenSetError,
    check_evidence,
    normalise,
    parse_golden,
)

DOCUMENTS = ["alpha.pdf", "beta.pdf"]


class WhitespaceTokenizer:
    """One token per run of non-space characters, so a quote's length is countable by eye."""

    def spans(self, text: str) -> list[tuple[int, int]]:
        return [match.span() for match in re.finditer(r"\S+", text)]


def valid_set() -> dict[str, list[dict[str, Any]]]:
    """Five answerable questions per document and eight unanswerable ones."""
    return {
        "answerable": [
            {
                "id": f"a{n:02}",
                "question": f"What is fact {n}?",
                "evidence": [{"document": DOCUMENTS[n % 2], "page": 1, "quote": f"fact {n}"}],
            }
            for n in range(10)
        ],
        "unanswerable": [
            {"id": f"u{n:02}", "question": f"What is missing {n}?", "reason": "Not stated."}
            for n in range(8)
        ],
    }


def to_toml(golden: dict[str, Any]) -> bytes:
    """Write the golden set's shape as TOML: plain keys first, then nested evidence tables."""
    lines: list[str] = []
    for kind, entries in golden.items():
        for entry in entries:
            lines.append(f"[[{kind}]]")
            nested: list[dict[str, Any]] = []
            for key, value in entry.items():
                if key == "evidence" and value and all(isinstance(v, dict) for v in value):
                    nested = value
                else:
                    lines.append(f"{key} = {json.dumps(value)}")
            for item in nested:
                lines.append(f"[[{kind}.evidence]]")
                lines.extend(f"{key} = {json.dumps(value)}" for key, value in item.items())
    return "\n".join(lines).encode()


def one_question(page: int, quote: str, question_id: str = "a01") -> AnswerableQuestion:
    return AnswerableQuestion(question_id, "What?", (Evidence("alpha.pdf", page, quote),))


def golden_of(*questions: AnswerableQuestion) -> GoldenSet:
    return GoldenSet(answerable=questions, unanswerable=(), sha256="")


def test_a_valid_set_parses_with_the_digest_of_its_file() -> None:
    data = to_toml(valid_set())

    golden = parse_golden(data, DOCUMENTS)

    assert len(golden.answerable) == 10
    assert len(golden.unanswerable) == 8
    assert golden.answerable[1] == AnswerableQuestion(
        "a01", "What is fact 1?", (Evidence("beta.pdf", 1, "fact 1"),)
    )
    assert golden.sha256 == hashlib.sha256(data).hexdigest()


def test_invalid_toml_is_rejected() -> None:
    with pytest.raises(GoldenSetError, match="not valid TOML"):
        parse_golden(b"[[answerable]\n", DOCUMENTS)


def test_an_unknown_field_is_rejected_rather_than_ignored() -> None:
    """A misspelt field would otherwise vanish, and with it the value it was meant to carry."""
    golden = valid_set()
    golden["answerable"][0]["evidence"][0]["quotes"] = "fact 0"

    with pytest.raises(GoldenSetError, match="a00 evidence: unknown quotes"):
        parse_golden(to_toml(golden), DOCUMENTS)


def test_an_unknown_top_level_field_is_rejected() -> None:
    with pytest.raises(GoldenSetError, match="the golden set: unknown notes"):
        parse_golden(b'notes = "draft"\n' + to_toml(valid_set()), DOCUMENTS)


def test_a_missing_field_is_rejected() -> None:
    golden = valid_set()
    del golden["unanswerable"][3]["reason"]

    with pytest.raises(GoldenSetError, match="u03: missing reason"):
        parse_golden(to_toml(golden), DOCUMENTS)


def test_blank_text_is_rejected() -> None:
    golden = valid_set()
    golden["answerable"][2]["question"] = "   "

    with pytest.raises(GoldenSetError, match="a02: question must be non-empty text"):
        parse_golden(to_toml(golden), DOCUMENTS)


def test_an_answerable_question_needs_evidence() -> None:
    golden = valid_set()
    golden["answerable"][0]["evidence"] = []

    with pytest.raises(GoldenSetError, match="a00: needs at least one"):
        parse_golden(to_toml(golden), DOCUMENTS)


def test_evidence_must_name_a_corpus_document() -> None:
    golden = valid_set()
    golden["answerable"][0]["evidence"][0]["document"] = "gamma.pdf"

    with pytest.raises(GoldenSetError, match="a00: evidence names gamma.pdf"):
        parse_golden(to_toml(golden), DOCUMENTS)


@pytest.mark.parametrize("page", [0, -1, "9", 9.5, True])
def test_a_page_must_be_a_whole_number_from_one(page: object) -> None:
    golden = valid_set()
    golden["answerable"][0]["evidence"][0]["page"] = page

    with pytest.raises(GoldenSetError, match="a00: page must be a whole number"):
        parse_golden(to_toml(golden), DOCUMENTS)


def test_ids_must_be_unique_across_both_kinds_of_question() -> None:
    golden = valid_set()
    golden["unanswerable"][0]["id"] = "a04"

    with pytest.raises(GoldenSetError, match="used more than once: a04"):
        parse_golden(to_toml(golden), DOCUMENTS)


def test_every_document_needs_exactly_five_answerable_questions() -> None:
    golden = valid_set()
    golden["answerable"][0]["evidence"][0]["document"] = "beta.pdf"

    with pytest.raises(GoldenSetError, match="alpha.pdf has 4; beta.pdf has 6"):
        parse_golden(to_toml(golden), DOCUMENTS)


def test_there_must_be_eight_unanswerable_questions() -> None:
    golden = valid_set()
    golden["unanswerable"].pop()

    with pytest.raises(GoldenSetError, match="found 7"):
        parse_golden(to_toml(golden), DOCUMENTS)


def test_quotes_compare_ignoring_case_and_whitespace() -> None:
    assert normalise("  The Quick\n\tbrown  FOX ") == "the quick brown fox"


def test_a_quote_on_its_page_passes() -> None:
    pages = {"alpha.pdf": ["Cover", "The  Quick\nbrown fox jumps."]}

    check_evidence(golden_of(one_question(2, "the quick brown fox")), pages, WhitespaceTokenizer())


def test_a_quote_on_another_page_is_reported_with_where_it_is() -> None:
    """The page is what a citation sends a reader to, so it must be exactly right."""
    pages = {"alpha.pdf": ["Cover", "Contents", "The quick brown fox."]}

    with pytest.raises(GoldenSetError, match="a01 .alpha.pdf, page 2.: .* it is on page 3"):
        check_evidence(golden_of(one_question(2, "quick brown fox")), pages, WhitespaceTokenizer())


def test_a_quote_not_in_the_document_is_reported() -> None:
    pages = {"alpha.pdf": ["The quick brown fox."]}

    with pytest.raises(GoldenSetError, match="it is not in the document"):
        check_evidence(golden_of(one_question(1, "lazy dog")), pages, WhitespaceTokenizer())


def test_a_page_past_the_end_of_the_document_is_reported() -> None:
    pages = {"alpha.pdf": ["The quick brown fox."]}

    with pytest.raises(GoldenSetError, match="has only 1 pages"):
        check_evidence(golden_of(one_question(2, "quick")), pages, WhitespaceTokenizer())


def test_a_quote_must_be_under_the_token_limit() -> None:
    words = [f"w{n}" for n in range(MAX_QUOTE_TOKENS)]
    pages = {"alpha.pdf": [" ".join(words)]}
    under = " ".join(words[:-1])
    at_limit = " ".join(words)

    check_evidence(golden_of(one_question(1, under)), pages, WhitespaceTokenizer())
    with pytest.raises(GoldenSetError, match=f"quote is {MAX_QUOTE_TOKENS} tokens"):
        check_evidence(golden_of(one_question(1, at_limit)), pages, WhitespaceTokenizer())


def test_every_fault_is_reported_at_once() -> None:
    """A set is fixed in one pass, not one fault per run."""
    pages = {"alpha.pdf": ["The quick brown fox."]}
    golden = golden_of(one_question(1, "lazy dog", "a01"), one_question(3, "quick", "a02"))

    with pytest.raises(GoldenSetError) as raised:
        check_evidence(golden, pages, WhitespaceTokenizer())

    assert "a01" in str(raised.value)
    assert "a02" in str(raised.value)


def test_the_quote_limit_keeps_every_quote_whole_within_one_chunk() -> None:
    """Consecutive chunks share at least OVERLAP_TOKENS, so a shorter span is never split.

    Raising the limit to the overlap or beyond would let a quote straddle a chunk
    boundary, and a retrieval miss would then be the chunker's doing.
    """
    assert MAX_QUOTE_TOKENS < OVERLAP_TOKENS
