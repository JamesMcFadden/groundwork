"""What answer generation promises, whichever backend provides it.

A generator receives a question and numbered passages, and answers in statements, each
citing the passage numbers it relies on. Structured statements rather than prose with
inline markers mean citations never have to be parsed out of text. A generator is not
trusted to cite correctly: its citations are for the caller to check against the
passages it was given.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.generation.context import Passage


@dataclass(frozen=True, slots=True)
class Statement:
    """One statement of an answer, with the passage numbers it cites."""

    text: str
    citations: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Generation:
    """A generator's answer, before its citations are checked.

    `insufficient_evidence` is the generator's own judgement that the passages do not
    answer the question. Token counts are None where a backend reports none.
    """

    statements: tuple[Statement, ...]
    insufficient_evidence: bool
    input_tokens: int | None = None
    output_tokens: int | None = None


class Generator(Protocol):
    def generate(self, question: str, passages: Sequence[Passage]) -> Generation: ...
