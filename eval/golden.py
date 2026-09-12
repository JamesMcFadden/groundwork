"""The golden set: questions with known answers, and the checks that keep it honest.

Structure is checked from the TOML alone: the fields each question needs, a corpus
document behind every piece of evidence, five answerable questions per document, and
eight unanswerable ones. Evidence is checked against the documents' parsed text: each
quote must appear on the page it names, and be short enough to lie whole within a chunk,
so a retrieval miss is never an artefact of where the chunker happened to cut.

Neither check reads chunks. Questions are written from the documents and validated
against the documents.
"""

import hashlib
import tomllib
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ingest.chunk import Tokenizer

GOLDEN_PATH = Path(__file__).parent / "golden.toml"

ANSWERABLE_PER_DOCUMENT = 5
UNANSWERABLE_COUNT = 8

# Consecutive chunks overlap by at least OVERLAP_TOKENS (64), so a shorter quote always
# lies whole within one of them. The margin allows for a quote tokenizing slightly
# differently on its own than inside its sentence.
MAX_QUOTE_TOKENS = 48

_TOP_LEVEL_KEYS = {"answerable", "unanswerable"}
_ANSWERABLE_KEYS = {"id", "question", "evidence"}
_EVIDENCE_KEYS = {"document", "page", "quote"}
_UNANSWERABLE_KEYS = {"id", "question", "reason"}


class GoldenSetError(Exception):
    """The golden set is malformed, or its evidence does not match the documents."""


@dataclass(frozen=True, slots=True)
class Evidence:
    """One place an answer is stated: a document, a page counted from 1, a verbatim quote.

    The page is the page's position in the PDF, not its printed number.
    """

    document: str
    page: int
    quote: str


@dataclass(frozen=True, slots=True)
class AnswerableQuestion:
    """A question whose answer the corpus states. Any one evidence location counts."""

    id: str
    question: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class UnanswerableQuestion:
    """A question the corpus cannot answer, with why, for whoever reviews a refusal."""

    id: str
    question: str
    reason: str


@dataclass(frozen=True, slots=True)
class GoldenSet:
    """The questions, and the digest of the file they came from, recorded with results."""

    answerable: tuple[AnswerableQuestion, ...]
    unanswerable: tuple[UnanswerableQuestion, ...]
    sha256: str


def normalise(text: str) -> str:
    """Casefold and collapse whitespace: how a quote is compared with document text.

    Casefolding forgives a quote capitalised differently at a sentence boundary, and
    collapsing whitespace forgives line breaks, without accepting different words.
    """
    return " ".join(text.casefold().split())


def load_golden(documents: Collection[str], path: Path = GOLDEN_PATH) -> GoldenSet:
    """Read and structurally validate the golden set against the corpus's filenames."""
    return parse_golden(path.read_bytes(), documents)


def parse_golden(data: bytes, documents: Collection[str]) -> GoldenSet:
    """Parse a golden set and check its structure. Raises `GoldenSetError` on any fault."""
    try:
        raw = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise GoldenSetError(f"not valid TOML: {exc}") from exc

    _require_keys(raw, _TOP_LEVEL_KEYS, "the golden set")
    answerable = tuple(_answerable(entry, documents) for entry in _tables(raw, "answerable"))
    unanswerable = tuple(_unanswerable(entry) for entry in _tables(raw, "unanswerable"))

    ids = Counter(
        [question.id for question in answerable] + [question.id for question in unanswerable]
    )
    duplicates = sorted(question_id for question_id, count in ids.items() if count > 1)
    if duplicates:
        raise GoldenSetError(f"question ids used more than once: {', '.join(duplicates)}")

    # A question counts toward the document of its first evidence location.
    per_document = Counter(question.evidence[0].document for question in answerable)
    wrong = [
        f"{document} has {per_document[document]}"
        for document in sorted(documents)
        if per_document[document] != ANSWERABLE_PER_DOCUMENT
    ]
    if wrong:
        raise GoldenSetError(
            f"each document needs {ANSWERABLE_PER_DOCUMENT} answerable questions: "
            + "; ".join(wrong)
        )
    if len(unanswerable) != UNANSWERABLE_COUNT:
        raise GoldenSetError(
            f"need {UNANSWERABLE_COUNT} unanswerable questions, found {len(unanswerable)}"
        )

    return GoldenSet(answerable, unanswerable, hashlib.sha256(data).hexdigest())


def check_evidence(
    golden: GoldenSet, pages: Mapping[str, Sequence[str]], tokenizer: Tokenizer
) -> None:
    """Check every quote against its document's parsed text, reporting all faults at once.

    `pages` maps each document to its pages' text in order, the first page at index 0,
    as `parse_pdf` returns them. The tokenizer must be the embedding model's, since the
    quote limit is measured against the chunker's overlap in that model's tokens.
    """
    problems = [
        problem
        for question in golden.answerable
        for evidence in question.evidence
        for problem in _evidence_problems(
            question.id, evidence, pages[evidence.document], tokenizer
        )
    ]
    if problems:
        raise GoldenSetError("evidence does not match the documents:\n" + "\n".join(problems))


def _evidence_problems(
    question_id: str, evidence: Evidence, pages: Sequence[str], tokenizer: Tokenizer
) -> list[str]:
    where = f"{question_id} ({evidence.document}, page {evidence.page})"
    problems: list[str] = []

    tokens = len(tokenizer.spans(evidence.quote))
    if tokens >= MAX_QUOTE_TOKENS:
        problems.append(f"{where}: quote is {tokens} tokens; it must be under {MAX_QUOTE_TOKENS}")

    if evidence.page > len(pages):
        problems.append(f"{where}: the document has only {len(pages)} pages")
        return problems

    quote = normalise(evidence.quote)
    if quote not in normalise(pages[evidence.page - 1]):
        found = [
            str(number) for number, text in enumerate(pages, start=1) if quote in normalise(text)
        ]
        detail = f"it is on page {', '.join(found)}" if found else "it is not in the document"
        problems.append(f"{where}: quote is not on that page; {detail}")
    return problems


def _require_keys(table: Mapping[str, Any], expected: set[str], where: str) -> None:
    """Reject missing and unknown keys alike: an unknown key is usually a misspelt one."""
    missing = sorted(expected - table.keys())
    unknown = sorted(table.keys() - expected)
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unknown:
            details.append(f"unknown {', '.join(unknown)}")
        raise GoldenSetError(f"{where}: {'; '.join(details)}")


def _tables(table: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = table[key]
    if not isinstance(value, list) or not all(isinstance(entry, dict) for entry in value):
        raise GoldenSetError(f"{key} must be an array of tables, written [[{key}]]")
    return value


def _string(table: Mapping[str, Any], key: str, where: str) -> str:
    value = table[key]
    if not isinstance(value, str) or not value.strip():
        raise GoldenSetError(f"{where}: {key} must be non-empty text")
    return value


def _answerable(entry: Mapping[str, Any], documents: Collection[str]) -> AnswerableQuestion:
    _require_keys(entry, _ANSWERABLE_KEYS, f"answerable question {entry.get('id', '?')}")
    question_id = _string(entry, "id", "an answerable question")
    question = _string(entry, "question", question_id)

    raw_evidence = entry["evidence"]
    if not isinstance(raw_evidence, list) or not raw_evidence:
        raise GoldenSetError(f"{question_id}: needs at least one [[answerable.evidence]]")
    return AnswerableQuestion(
        id=question_id,
        question=question,
        evidence=tuple(_evidence(item, question_id, documents) for item in raw_evidence),
    )


def _evidence(item: Any, question_id: str, documents: Collection[str]) -> Evidence:
    if not isinstance(item, dict):
        raise GoldenSetError(
            f"{question_id}: evidence must be tables, written [[answerable.evidence]]"
        )
    _require_keys(item, _EVIDENCE_KEYS, f"{question_id} evidence")
    document = _string(item, "document", question_id)
    if document not in documents:
        raise GoldenSetError(
            f"{question_id}: evidence names {document}, which is not in the corpus"
        )
    page = item["page"]
    # TOML booleans are Python bools, and bool is a subclass of int.
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise GoldenSetError(f"{question_id}: page must be a whole number from 1, got {page!r}")
    return Evidence(document=document, page=page, quote=_string(item, "quote", question_id))


def _unanswerable(entry: Mapping[str, Any]) -> UnanswerableQuestion:
    _require_keys(entry, _UNANSWERABLE_KEYS, f"unanswerable question {entry.get('id', '?')}")
    question_id = _string(entry, "id", "an unanswerable question")
    return UnanswerableQuestion(
        id=question_id,
        question=_string(entry, "question", question_id),
        reason=_string(entry, "reason", question_id),
    )
