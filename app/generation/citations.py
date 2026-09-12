"""Checking a generator's citations against the passages it was given.

A citation is valid when it names a passage supplied for this request, and anything else
is dropped before an answer is returned or recorded. Rejections are counted rather than
silently corrected: how often the model cites a passage it was never given is the
measurement that justifies checking at all.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.generation.context import Passage
from app.generation.generator import Generation, Statement

# A statement ending in one of these gets its markers before the mark rather than after.
_CLOSING_MARKS = ".!?"


@dataclass(frozen=True, slots=True)
class CheckedAnswer:
    """An answer reduced to statements whose every citation names a supplied passage.

    `cited` lists each valid passage number once, in the order the answer first cites
    it. `invalid_citations` counts the distinct numbers rejected. No statements means
    nothing in the answer survived checking.
    """

    statements: tuple[Statement, ...]
    cited: tuple[int, ...]
    invalid_citations: int

    @property
    def text(self) -> str:
        """The statements as prose, each carrying its [n] markers."""
        return " ".join(_with_markers(statement) for statement in self.statements)


def check_citations(generation: Generation, passages: Sequence[Passage]) -> CheckedAnswer:
    """Keep only citations of supplied passages, and only statements that keep one.

    A statement left citing nothing is dropped whole: every claim must rest on a passage,
    and one citing none, or only passages that were never supplied, rests on nothing.
    """
    supplied = {passage.number for passage in passages}
    statements: list[Statement] = []
    cited: dict[int, None] = {}
    invalid: set[int] = set()
    for statement in generation.statements:
        valid = tuple(dict.fromkeys(n for n in statement.citations if n in supplied))
        invalid.update(n for n in statement.citations if n not in supplied)
        if valid:
            statements.append(Statement(text=statement.text, citations=valid))
            cited.update(dict.fromkeys(valid))
    return CheckedAnswer(
        statements=tuple(statements), cited=tuple(cited), invalid_citations=len(invalid)
    )


def _with_markers(statement: Statement) -> str:
    markers = "".join(f"[{number}]" for number in statement.citations)
    text = statement.text.rstrip()
    if text and text[-1] in _CLOSING_MARKS:
        return f"{text[:-1]} {markers}{text[-1]}"
    return f"{text} {markers}"
